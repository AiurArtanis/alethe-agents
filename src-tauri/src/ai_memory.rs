//! ai-memory: long-term memory for the agents Alethe launches.
//!
//! Two halves. The older one answers queries: `ai_memory_detect` finds the binary and health-checks
//! its loopback endpoint, and the three config writers register its MCP server per agent — Claude
//! through an ephemeral `--mcp-config` so nothing is left pointing at a dead endpoint, Codex and
//! OpenCode through config files in the repository. `useXtermSession` calls them at launch.
//!
//! The newer one manages the service itself — which release to fetch for this machine, and the
//! `serve` child — shaped after `router9.rs`, which does the same job for 9router.

pub const AI_MEMORY_VERSION: &str = "2.4.0";
const RELEASES: &str = "https://github.com/akitaonrails/ai-memory/releases/download";

#[derive(Debug, Clone, PartialEq)]
pub struct ReleaseAsset {
    pub file: String,
    pub url: String,
    pub sha256_url: String,
}

/// The asset for this machine, or `None` when upstream publishes no build for it.
///
/// Windows on ARM is the real case: every other platform Alethe supports has one.
pub fn release_asset(os: &str, arch: &str) -> Option<ReleaseAsset> {
    let file = match (os, arch) {
        ("windows", "x86_64") => "ai-memory-windows-x86_64.zip",
        ("linux", "x86_64") => "ai-memory-linux-x86_64.tar.gz",
        ("linux", "aarch64") => "ai-memory-linux-aarch64.tar.gz",
        ("macos", "x86_64") => "ai-memory-macos-x86_64.tar.gz",
        ("macos", "aarch64") => "ai-memory-macos-aarch64.tar.gz",
        _ => return None,
    };
    let url = format!("{RELEASES}/v{AI_MEMORY_VERSION}/{file}");
    Some(ReleaseAsset {
        file: file.to_string(),
        sha256_url: format!("{url}.sha256"),
        url,
    })
}

use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::AppHandle;

use crate::git_control::{hide_console, repository_root};
use crate::paths::profile_data_dir;

const DEFAULT_COMMAND: &str = "ai-memory";

/// health-check de "running".
const DEFAULT_ENDPOINT: &str = "127.0.0.1:49374";

const MCP_KEY: &str = "ai-memory";

pub fn binary_name() -> &'static str {
    if cfg!(windows) {
        "ai-memory.exe"
    } else {
        "ai-memory"
    }
}

pub fn install_dir(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(profile_data_dir(app)?.join("tools").join("ai-memory"))
}

/// The copy Alethe installed, if it is there.
pub fn managed_binary(app: &AppHandle) -> Option<String> {
    let path = install_dir(app).ok()?.join(binary_name());
    path.is_file().then(|| path.to_string_lossy().to_string())
}

/// Which binary to run: what the caller asked for, else the managed copy by full path, else the
/// bare name for `PATH` to resolve.
///
/// The managed copy lives in the profile folder, so it has to travel as a path — the bare name
/// would not resolve for the agents the config writers hand it to.
pub fn pick_command(managed: Option<String>, explicit: Option<String>) -> String {
    explicit
        .or(managed)
        .unwrap_or_else(|| DEFAULT_COMMAND.to_string())
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AiMemoryStatus {
    installed: bool,
    /// The server answers on the loopback endpoint.
    running: bool,
    command: String,
    endpoint: String,
    version: Option<String>,
    /// This is the copy Alethe installed, not one found on `PATH`.
    managed: bool,
    /// Upstream publishes a build for this machine. False on Windows ARM64.
    supported: bool,
}

fn short_hash(input: &str) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    input.hash(&mut hasher);
    format!("{:x}", hasher.finish())
}

/// interface oficial for pinada (stdio via `ai-memory mcp` vs. transporte
/// HTTP/SSE no endpoint loopback). Por ora usa o bridge stdio, coerente com o

/// o root como argumento.
fn mcp_server_spec(command: &str) -> Value {
    serde_json::json!({
        "command": command,
        "args": [ "mcp" ]
    })
}

/// causa do health-check.
#[tauri::command]
pub fn ai_memory_detect(app: AppHandle, command: Option<String>) -> Result<AiMemoryStatus, String> {
    let managed = managed_binary(&app);
    let cmd = pick_command(managed.clone(), command);

    let mut probe = Command::new(&cmd);
    probe.arg("--version");
    hide_console(&mut probe);
    let (installed, version) = match probe.output() {
        Ok(output) if output.status.success() => {
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            (true, (!version.is_empty()).then_some(version))
        }
        _ => (false, None),
    };

    let running = endpoint_alive(DEFAULT_ENDPOINT);

    Ok(AiMemoryStatus {
        installed,
        running,
        managed: managed.as_deref() == Some(cmd.as_str()),
        supported: release_asset(std::env::consts::OS, std::env::consts::ARCH).is_some(),
        command: cmd,
        endpoint: DEFAULT_ENDPOINT.to_string(),
        version,
    })
}

fn endpoint_alive(endpoint: &str) -> bool {
    let Ok(mut addrs) = endpoint.to_socket_addrs() else {
        return false;
    };
    addrs.any(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(250)).is_ok())
}

#[tauri::command]
pub fn ai_memory_mcp_config_path(
    app: AppHandle,
    repo: String,
    command: Option<String>,
) -> Result<String, String> {
    let root = repository_root(&repo)?;
    let cmd = pick_command(managed_binary(&app), command);
    let config = serde_json::json!({

        "mcpServers": { (MCP_KEY): mcp_server_spec(&cmd) }
    });
    let file_name = format!(
        "alethe-ai-memory-mcp-{}.json",
        short_hash(&root.to_string_lossy())
    );
    let path = std::env::temp_dir().join(file_name);
    let body = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())?;
    std::fs::write(&path, body).map_err(|e| format!("write_failed:{e}"))?;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn ai_memory_opencode_config_write(
    app: AppHandle,
    repo: String,
    command: Option<String>,
) -> Result<(), String> {
    let _guard = crate::provider_common::opencode_json_lock()
        .lock()
        .map_err(|_| "opencode.json lock poisoned".to_string())?;
    let root = repository_root(&repo)?;
    let cmd = pick_command(managed_binary(&app), command);
    let path = root.join("opencode.json");

    let mut config: serde_json::Map<String, Value> = if path.is_file() {
        let raw = std::fs::read_to_string(&path).map_err(|e| format!("read_failed:{e}"))?;
        match serde_json::from_str::<Value>(&raw) {
            Ok(Value::Object(map)) => map,

            _ => return Ok(()),
        }
    } else {
        let mut map = serde_json::Map::new();
        map.insert(
            "$schema".to_string(),
            Value::String("https://opencode.ai/config.json".to_string()),
        );
        map
    };

    let mcp = config
        .entry("mcp".to_string())
        .or_insert_with(|| Value::Object(serde_json::Map::new()));
    if let Value::Object(mcp_map) = mcp {
        mcp_map.insert(
            MCP_KEY.to_string(),
            serde_json::json!({
                "type": "local",
                "command": [cmd, "mcp"],
                "enabled": true,
            }),
        );
    }

    let body = serde_json::to_string_pretty(&Value::Object(config)).map_err(|e| e.to_string())?;
    std::fs::write(&path, body).map_err(|e| format!("write_failed:{e}"))
}

#[tauri::command]
pub fn ai_memory_codex_config_write(
    app: AppHandle,
    repo: String,
    command: Option<String>,
) -> Result<(), String> {
    let root = repository_root(&repo)?;
    let cmd = pick_command(managed_binary(&app), command);
    let codex_dir = root.join(".codex");
    std::fs::create_dir_all(&codex_dir).map_err(|e| format!("mkdir_failed:{e}"))?;
    let path = codex_dir.join("config.toml");

    let existing = if path.is_file() {
        std::fs::read_to_string(&path).map_err(|e| format!("read_failed:{e}"))?
    } else {
        String::new()
    };

    let header = format!("[mcp_servers.\"{MCP_KEY}\"]");
    let mut kept_lines: Vec<&str> = Vec::new();
    let mut skipping = false;
    for line in existing.lines() {
        let trimmed = line.trim();
        if trimmed == header {
            skipping = true;
            continue;
        }
        if skipping && trimmed.starts_with('[') {
            skipping = false;
        }
        if !skipping {
            kept_lines.push(line);
        }
    }
    let mut body = kept_lines.join("\n");
    if !body.is_empty() && !body.ends_with('\n') {
        body.push('\n');
    }

    let toml_escape = |s: &str| s.replace('\\', "\\\\").replace('"', "\\\"");
    let cmd_toml = toml_escape(&cmd);
    body.push_str(&format!(
        "\n{header}\ncommand = \"{cmd_toml}\"\nargs = [\"mcp\"]\n",
    ));

    std::fs::write(&path, body).map_err(|e| format!("write_failed:{e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_platform_alethe_supports_gets_the_matching_asset() {
        for (os, arch, expected) in [
            ("windows", "x86_64", "ai-memory-windows-x86_64.zip"),
            ("linux", "x86_64", "ai-memory-linux-x86_64.tar.gz"),
            ("linux", "aarch64", "ai-memory-linux-aarch64.tar.gz"),
            ("macos", "x86_64", "ai-memory-macos-x86_64.tar.gz"),
            ("macos", "aarch64", "ai-memory-macos-aarch64.tar.gz"),
        ] {
            let asset = release_asset(os, arch).unwrap_or_else(|| panic!("{os}/{arch}"));
            assert_eq!(asset.file, expected);
            assert!(asset.url.ends_with(expected), "{}", asset.url);
            assert_eq!(asset.sha256_url, format!("{}.sha256", asset.url));
            assert!(asset.url.starts_with("https://"), "{}", asset.url);
        }
    }

    #[test]
    fn a_machine_upstream_does_not_build_for_gets_no_asset() {
        // Windows on ARM: the release has no such build. Returning None is what lets the UI say so
        // instead of offering a button that downloads a 404.
        assert!(release_asset("windows", "aarch64").is_none());
        assert!(release_asset("freebsd", "x86_64").is_none());
    }

    #[test]
    fn an_explicit_command_always_wins() {
        // The caller named a binary; nothing may second-guess that.
        assert_eq!(
            pick_command(Some("/profile/ai-memory".into()), Some("/usr/bin/ai-memory".into())),
            "/usr/bin/ai-memory"
        );
        assert_eq!(pick_command(None, Some("/usr/bin/ai-memory".into())), "/usr/bin/ai-memory");
    }

    #[test]
    fn the_copy_alethe_installed_travels_as_a_full_path() {
        // It lives in the profile folder, which is not on PATH: handing the agent the bare name
        // would give it a command it cannot resolve, and memory would silently never work.
        assert_eq!(pick_command(Some("/profile/ai-memory".into()), None), "/profile/ai-memory");
    }

    #[test]
    fn with_nothing_installed_the_bare_name_is_tried() {
        // PATH may still hold one the person installed themselves.
        assert_eq!(pick_command(None, None), DEFAULT_COMMAND);
    }
}
