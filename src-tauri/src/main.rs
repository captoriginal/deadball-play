#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    ffi::OsString,
    fs::{self, OpenOptions},
    io::Read,
    io::Write as IoWrite,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
};
use tauri::{Emitter, Manager, RunEvent, WindowEvent};

type SharedChild = Arc<Mutex<Option<Child>>>;

#[derive(serde::Serialize)]
struct BackendResponse {
    status: u16,
    body: Vec<u8>,
    content_type: Option<String>,
    content_disposition: Option<String>,
}

fn to_backend_response(response: ureq::Response) -> Result<BackendResponse, String> {
    let status = response.status();
    let content_type = response.header("content-type").map(ToOwned::to_owned);
    let content_disposition = response
        .header("content-disposition")
        .map(ToOwned::to_owned);
    let mut reader = response.into_reader();
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(BackendResponse {
        status,
        body: bytes,
        content_type,
        content_disposition,
    })
}

fn log_backend(msg: &str) {
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/deadball-backend.log")
    {
        let _ = writeln!(f, "{}", msg);
    }
}

fn python_cmd(backend_dir: &Path) -> PathBuf {
    // Prefer a bundled backend-local venv in all builds.
    let backend_venv = backend_dir.join(".venv/bin/python");
    if backend_venv.exists() {
        return backend_venv;
    }

    // Debug mode can still use the repo venv for local iteration.
    let repo_root_venv = backend_dir.join("../.venv/bin/python");
    if cfg!(debug_assertions) && repo_root_venv.exists() {
        return repo_root_venv;
    }

    env::var_os("PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("python3"))
}

fn backend_pythonpath(backend_dir: &Path) -> Option<OsString> {
    let mut paths = vec![backend_dir.to_path_buf()];
    let generator_src = backend_dir.join("deadball_generator/src");
    if generator_src.exists() {
        paths.push(generator_src);
    }
    let core_src = backend_dir.join("deadball_core/src");
    if core_src.exists() {
        paths.push(core_src);
    }
    env::join_paths(paths).ok()
}

fn spawn_backend(backend_dir: &Path) -> std::io::Result<Child> {
    let mut cmd = Command::new(python_cmd(backend_dir));
    cmd.current_dir(backend_dir).args([
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--log-level",
        "warning",
    ]);
    if let Some(pythonpath) = backend_pythonpath(backend_dir) {
        cmd.env("PYTHONPATH", pythonpath);
    }
    // Force a writable sqlite location for packaged app runs.
    // This also avoids backend/.env relative path assumptions.
    let db_path = backend_dir.join("deadball_dev.db");
    cmd.env("DATABASE_URL", format!("sqlite:///{}", db_path.display()));

    // Write backend stdout/stderr into the shared backend log for packaged-app debugging.
    let stdout_log = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/deadball-backend.log")?;
    let stderr_log = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/deadball-backend.log")?;
    cmd.stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log));
    cmd.spawn()
}

fn launch_backend(proc_ref: SharedChild, backend_dir: PathBuf, app_handle: tauri::AppHandle) {
    thread::spawn(move || match spawn_backend(&backend_dir) {
        Ok(mut child) => {
            // Catch immediate startup crashes (import errors, missing deps, etc.).
            thread::sleep(std::time::Duration::from_millis(350));
            match child.try_wait() {
                Ok(Some(status)) => {
                    let msg = format!("Backend exited immediately after spawn with status: {status}");
                    log_backend(&msg);
                    eprintln!("{msg}");
                    let _ = app_handle.emit("backend-error", msg);
                }
                Ok(None) => {
                    *proc_ref.lock().unwrap() = Some(child);
                }
                Err(err) => {
                    let msg = format!("Failed to inspect backend process state: {err}");
                    log_backend(&msg);
                    eprintln!("{msg}");
                    let _ = app_handle.emit("backend-error", msg);
                }
            }
        }
        Err(err) => {
            let msg = format!("Failed to start backend: {err}");
            eprintln!("{msg}");
            log_backend(&msg);
            let _ = app_handle.emit("backend-error", err.to_string());
        }
    });
}

fn terminate_backend(proc_ref: &SharedChild) {
    if let Some(mut child) = proc_ref.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[tauri::command]
fn save_scorecard_pdf(path: String, bytes: Vec<u8>) -> Result<(), String> {
    fs::write(path, bytes).map_err(|e| e.to_string())
}

#[tauri::command]
fn backend_request(
    method: String,
    path: String,
    body: Option<String>,
    content_type: Option<String>,
) -> Result<BackendResponse, String> {
    let url = format!("http://127.0.0.1:8000{path}");
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(std::time::Duration::from_secs(5))
        .timeout_read(std::time::Duration::from_secs(45))
        .timeout_write(std::time::Duration::from_secs(45))
        .build();

    // Backend can take a moment to bind after app startup.
    let attempts = 20;
    let retry_delay_ms = 200;
    for attempt in 1..=attempts {
        let mut req = agent.request(&method, &url);
        if let Some(ct) = content_type.as_deref() {
            req = req.set("content-type", ct);
        }

        let response_result = if let Some(body) = body.as_deref() {
            req.send_string(body)
        } else {
            req.call()
        };

        match response_result {
            Ok(resp) => return to_backend_response(resp),
            Err(ureq::Error::Status(_, resp)) => return to_backend_response(resp),
            Err(ureq::Error::Transport(err)) => {
                if attempt == attempts {
                    return Err(format!(
                        "{url}: Connection Failed after {attempts} attempts: {err}"
                    ));
                }
                thread::sleep(std::time::Duration::from_millis(retry_delay_ms));
            }
        }
    }

    Err(format!("{url}: Connection Failed"))
}

fn backend_is_usable(backend_dir: &Path) -> bool {
    if !backend_dir.join("app/main.py").exists() {
        return false;
    }
    if cfg!(debug_assertions) {
        return true;
    }
    backend_dir.join(".venv/bin/python").exists()
}

fn prepare_backend(app: &tauri::App) -> PathBuf {
    // Allow explicit backend overrides.
    if let Some(custom_backend) = env::var_os("DEADBALL_BACKEND_DIR").map(PathBuf::from) {
        if custom_backend.exists() {
            log_backend(&format!(
                "Using backend from DEADBALL_BACKEND_DIR: {}",
                custom_backend.display()
            ));
            return custom_backend;
        }
    }

    // Choose an app data location for a writable backend copy.
    let app_data_backend = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("backend"))
        .join("backend");

    if app_data_backend.exists() && backend_is_usable(&app_data_backend) {
        return app_data_backend;
    }

    // Prefer a known development absolute path in debug mode only.
    if cfg!(debug_assertions) {
        let dev_absolute = PathBuf::from("/Users/steve/dev/apps/deadball-play/backend");
        if dev_absolute.exists() {
            return dev_absolute;
        }
    }

    // Extract from bundled resources/backend-template.tar.gz into app data.
    if let Ok(res_dir) = app.path().resource_dir() {
        let archive = {
            let direct = res_dir.join("backend-template.tar.gz");
            if direct.exists() {
                Some(direct)
            } else {
                let nested = res_dir.join("resources/backend-template.tar.gz");
                if nested.exists() {
                    Some(nested)
                } else {
                    None
                }
            }
        };
        if let Some(archive) = archive {
            let _ = fs::create_dir_all(app_data_backend.parent().unwrap_or(&app_data_backend));
            let status = Command::new("tar")
                .args([
                    "-xzf",
                    archive
                        .to_str()
                        .unwrap_or("backend-template.tar.gz"),
                    "-C",
                    app_data_backend
                        .parent()
                        .unwrap_or(&app_data_backend)
                        .to_str()
                        .unwrap_or("."),
                ])
                .status();
            match status {
                Ok(s) if s.success() => {
                    if backend_is_usable(&app_data_backend) {
                        return app_data_backend;
                    }
                    log_backend(&format!(
                        "Extracted backend template but backend is not usable: {}",
                        app_data_backend.display()
                    ));
                }
                Ok(s) => log_backend(&format!("tar exited with status: {}", s)),
                Err(err) => log_backend(&format!("Failed to run tar: {}", err)),
            }
        } else {
            log_backend("No backend-template found in resources");
        }
    } else {
        log_backend("No resource dir available");
    }

    if cfg!(debug_assertions) {
        // Debug fallback to dev-relative path (repo layout).
        if let Ok(exe_path) = env::current_exe() {
            if let Some(parent) = exe_path.parent() {
                let dev = parent.join("../backend");
                if dev.exists() {
                    return dev;
                }
            }
        }
        return PathBuf::from("../backend");
    }
    app_data_backend
}

fn main() {
    let backend_proc: SharedChild = Arc::new(Mutex::new(None));

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![save_scorecard_pdf, backend_request])
        .setup({
            let backend_proc = backend_proc.clone();
            move |app| {
                let backend_path = prepare_backend(app);
                launch_backend(backend_proc.clone(), backend_path, app.handle().clone());
                Ok(())
            }
        })
        .on_window_event({
            let backend_proc = backend_proc.clone();
            move |_window, event| {
                if let WindowEvent::CloseRequested { .. } = event {
                    terminate_backend(&backend_proc);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    let backend_proc_for_run = backend_proc.clone();
    app.run(move |_app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            terminate_backend(&backend_proc_for_run);
        }
    });

    terminate_backend(&backend_proc);
}
