use super::{engine::require_approved_source, media::byte_range};
use serde_json::json;
use std::{collections::HashSet, path::PathBuf};

#[test]
fn ranges_support_browser_open_ended_and_suffix_requests() {
    assert_eq!(byte_range("bytes=3-", 10), Some((3, 9)));
    assert_eq!(byte_range("bytes=-3", 10), Some((7, 9)));
    assert_eq!(byte_range("bytes=8-50", 10), Some((8, 9)));
}

#[test]
fn invalid_or_unsatisfiable_ranges_are_rejected() {
    for value in ["bytes=10-", "bytes=5-4", "bytes=-0", "bytes=0-1,4-5", "items=0-1"] {
        assert_eq!(byte_range(value, 10), None);
    }
}

#[test]
fn native_requests_cannot_probe_unselected_paths() {
    let mut request = json!({"action": "probe", "source": "unauthorized.mp4"});
    assert!(require_approved_source(&mut request, &HashSet::<PathBuf>::new()).is_err());
}

#[test]
fn an_existing_job_can_resume_without_a_new_file_selection() {
    let mut request = json!({"action": "run", "job_id": "previous-job"});
    assert!(require_approved_source(&mut request, &HashSet::<PathBuf>::new()).is_ok());
}

fn project_fixture() -> (PathBuf, PathBuf, String) {
    use std::{fs, time::{SystemTime, UNIX_EPOCH}};
    let nonce = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
    let data = std::env::temp_dir().join(format!("dubbing-native-{}-{nonce}", std::process::id()));
    let directory = data.join("video-ab12");
    let id = "0123456789abcdef0123456789abcdef".to_owned();
    fs::create_dir_all(&directory).unwrap();
    fs::write(directory.join(".dubbing-project"), json!({"version":1,"id":id}).to_string()).unwrap();
    fs::write(directory.join("project.json"), json!({"id":id,"source":"external.mp4"}).to_string()).unwrap();
    fs::write(data.join("project-index.json"), json!({"version":1,"projects":{&id:directory}}).to_string()).unwrap();
    (data, directory, id)
}

#[test]
fn native_project_actions_require_index_and_matching_ownership() {
    let (data, directory, id) = project_fixture();
    assert_eq!(super::projects::owned_project(&data, &id).unwrap(), dunce::canonicalize(&directory).unwrap());
    assert!(super::projects::owned_project(&data, "../../outside").is_err());
    std::fs::write(directory.join(".dubbing-project"), json!({"version":1,"id":"another"}).to_string()).unwrap();
    assert!(super::projects::owned_project(&data, &id).is_err());
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
#[cfg(debug_assertions)]
fn dev_project_index_cannot_authorize_an_external_owned_project() {
    let (data, _, id) = project_fixture();
    let (foreign_data, foreign_directory, _) = project_fixture();
    let manifest = foreign_directory.join("project.json");
    let original = std::fs::read(&manifest).unwrap();
    std::fs::write(data.join("project-index.json"), json!({"version":1,"projects":{&id:foreign_directory}}).to_string()).unwrap();
    let error = super::projects::owned_project(&data, &id).unwrap_err();
    assert!(error.contains(".dev/projects"));
    assert_eq!(std::fs::read(manifest).unwrap(), original);
    std::fs::remove_dir_all(data).unwrap();
    std::fs::remove_dir_all(foreign_data).unwrap();
}

#[test]
fn project_audio_registration_cannot_escape_owned_folder() {
    let (data, directory, id) = project_fixture();
    let inside = directory.join("voice.wav");
    let outside = data.join("outside.wav");
    std::fs::write(&inside, b"fixture").unwrap();
    std::fs::write(&outside, b"fixture").unwrap();
    let job = json!({"id":id,"directory":directory});
    assert!(super::projects::owned_media_path(&data, &job, &inside).is_some());
    assert!(super::projects::owned_media_path(&data, &job, &outside).is_none());
    let forged = json!({"id":"ffffffffffffffffffffffffffffffff","directory":data});
    assert!(super::projects::owned_media_path(&data, &forged, &outside).is_none());
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
fn forgetting_project_keeps_files_and_marks_legacy_id_hidden() {
    let (data, directory, id) = project_fixture();
    super::projects::unregister_project(&data, &id).unwrap();
    assert!(directory.join("project.json").is_file());
    let index: serde_json::Value = serde_json::from_slice(&std::fs::read(data.join("project-index.json")).unwrap()).unwrap();
    assert!(index["projects"].get(&id).is_none());
    assert!(index["forgotten"].as_array().unwrap().contains(&json!(id)));
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
fn imported_artifacts_require_native_picker_approval() {
    let (data, directory, _) = project_fixture();
    let transcript = directory.join("lines.srt");
    std::fs::write(&transcript, b"fixture").unwrap();
    let mut request = json!({"action":"import_artifact","stage":"transcribe","path":transcript});
    assert!(super::projects::require_approved_artifact(&mut request, &HashSet::new()).is_err());
    let approved = HashSet::from([dunce::canonicalize(transcript).unwrap()]);
    assert!(super::projects::require_approved_artifact(&mut request, &approved).is_ok());
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
fn cleaning_project_keeps_durable_source_and_artifacts_with_temp_like_names() {
    let (data, directory, id) = project_fixture();
    let source_dir = directory.join("01-source");
    let audio_dir = directory.join("02-audio");
    let output_dir = directory.join("07-output");
    for path in [&source_dir, &audio_dir, &output_dir] { std::fs::create_dir_all(path).unwrap(); }
    let source = source_dir.join("my.part.mp4");
    let durable = audio_dir.join("selected.part.wav");
    let temporary = audio_dir.join("source.pending.part.wav");
    let exported = output_dir.join("my.part-video.mp4");
    for path in [&source, &durable, &temporary, &exported] { std::fs::write(path, b"fixture").unwrap(); }
    std::fs::write(directory.join("project.json"), json!({"id":id,"source":source,"artifacts":{"source_audio":"02-audio/selected.part.wav"},"exports":[{"path":"07-output/my.part-video.mp4"}]}).to_string()).unwrap();
    let result = super::projects::handle(&data, "clean_project", &id).unwrap();
    assert!(source.is_file());
    assert!(durable.is_file());
    assert!(exported.is_file());
    assert!(!temporary.exists());
    assert_eq!(result["removed"], 1);
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
fn deleting_parent_project_rejects_nested_project_before_recycling() {
    let (data, directory, id) = project_fixture();
    let child = directory.join("another-project");
    std::fs::create_dir(&child).unwrap();
    std::fs::write(child.join(".dubbing-project"), json!({"version":1,"id":"ffffffffffffffffffffffffffffffff"}).to_string()).unwrap();
    let error = super::projects::handle(&data, "delete_project", &id).unwrap_err();
    assert!(error.contains("dự án khác"));
    assert!(directory.join("project.json").is_file());
    assert!(child.join(".dubbing-project").is_file());
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
fn project_reopening_requires_a_native_selected_directory() {
    let (data, directory, _) = project_fixture();
    let mut request = json!({"action":"open_project","path":directory});
    assert!(super::projects::require_approved_directory(&mut request, &HashSet::new()).is_err());
    let approved = HashSet::from([dunce::canonicalize(&directory).unwrap()]);
    assert!(super::projects::require_approved_directory(&mut request, &approved).is_ok());
    std::fs::remove_dir_all(data).unwrap();
}

#[test]
#[cfg(debug_assertions)]
fn debug_requests_ignore_old_profile_and_renderer_tool_settings() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().unwrap().join(".dev");
    let mut request = json!({"data_dir":"C:\\old-profile","settings":{
        "dev_mode":false,"dev_root":"C:\\old-profile","python_path":"C:\\Python\\python.exe",
        "ffmpeg_path":"C:\\global\\ffmpeg.exe","ffprobe_path":"C:\\global\\ffprobe.exe",
        "tts_model_path":"C:\\old-models","projects_dir":"C:\\Videos","output_dir":"C:\\exports",
        "ollama_host":"https://foreign.example:11434","ollama_owned":true,"ollama_model":"qwen2.5:7b"
    }});
    super::profile::apply_request(&mut request, &root, false).unwrap();
    assert_eq!(super::profile::dev_root(), Some(root.clone()));
    assert_eq!(request["data_dir"], json!(root));
    for (key, relative) in [("python_path","venv/Scripts/python.exe"), ("ffmpeg_path","tools/ffmpeg/bin/ffmpeg.exe"),
        ("ffprobe_path","tools/ffmpeg/bin/ffprobe.exe"), ("tts_model_path","models/vieneu"),
        ("projects_dir","projects"), ("output_dir","output")] {
        assert_eq!(request["settings"][key], json!(root.join(relative)), "{key}");
    }
    assert_eq!(request["settings"]["dev_mode"], true);
    assert_eq!(request["settings"]["dev_root"], json!(root));
    assert_eq!(request["settings"]["ollama_host"], "127.0.0.1:11435");
    assert_eq!(request["settings"]["ollama_owned"], false);
    assert_eq!(request["settings"]["ollama_model"], "qwen2.5:7b");
}

#[test]
fn dev_child_environment_keeps_profile_models_and_caches_inside_checkout() {
    let (root, _, _) = project_fixture();
    let original_profile = std::env::var_os("USERPROFILE");
    let mut command = std::process::Command::new("not-executed");
    for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "PYTHONPYCACHEPREFIX", "PYLAUNCHER_ALLOW_INSTALL",
        "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN", "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT",
        "PIP_LOG", "PIP_BUILD_TRACKER", "PIP_PYTHON", "PIP_SRC", "PIP_CONFIG_FILE", "TRANSFORMERS_CACHE", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE",
        "HF_TOKEN_PATH", "TORCH_EXTENSIONS_DIR", "HF_ASSETS_CACHE", "HUGGINGFACE_ASSETS_CACHE", "HF_XET_CACHE"] {
        command.env(key, "C:\\old-global-profile");
    }
    super::profile::apply_child(&mut command, &root).unwrap();
    let configured: std::collections::HashMap<_, _> = command.get_envs()
        .map(|(key,value)| (key.to_string_lossy().into_owned(), value.map(|value|value.to_string_lossy().into_owned()))).collect();
    for (key, relative) in [("USERPROFILE","profile"), ("APPDATA","profile/AppData/Roaming"),
        ("LOCALAPPDATA","profile/AppData/Local"), ("OLLAMA_MODELS","models/ollama"),
        ("PIP_CACHE_DIR","cache/pip"), ("HF_HOME","cache/huggingface"), ("HF_HUB_CACHE","cache/huggingface/hub"),
        ("HUGGINGFACE_HUB_CACHE","cache/huggingface/hub"), ("TORCH_HOME","cache/torch"), ("TORCH_EXTENSIONS_DIR","cache/torch_extensions"),
        ("HF_ASSETS_CACHE","cache/huggingface/assets"), ("HUGGINGFACE_ASSETS_CACHE","cache/huggingface/assets"), ("HF_XET_CACHE","cache/huggingface/xet"),
        ("XDG_CACHE_HOME","cache"), ("UV_CACHE_DIR","cache/uv"), ("NUMBA_CACHE_DIR","cache/numba"),
        ("GRADIO_TEMP_DIR","cache/gradio"), ("PYTHONUSERBASE","profile/python"), ("PYTHONPYCACHEPREFIX","cache/pycache"),
        ("TEMP","temp"), ("TMP","temp")] {
        assert_eq!(configured.get(key), Some(&Some(root.join(relative).to_string_lossy().into_owned())), "{key}");
        assert!(root.join(relative).is_dir(), "{key} must exist before child creation");
    }
    assert_eq!(configured.get("OLLAMA_HOST"), Some(&Some("127.0.0.1:11435".to_owned())));
    assert_eq!(configured.get("OLLAMA_NO_CLOUD"), Some(&Some("1".to_owned())));
    assert_eq!(configured.get("AIVD_DEV_MODE"), Some(&Some("1".to_owned())));
    for key in ["PYTHONHOME", "PYTHONPATH", "PYLAUNCHER_ALLOW_INSTALL", "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN",
        "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT", "PIP_LOG", "PIP_BUILD_TRACKER", "PIP_PYTHON", "PIP_SRC", "TRANSFORMERS_CACHE"] {
        assert_eq!(configured.get(key), Some(&None), "{key}");
    }
    assert_eq!(configured.get("HF_TOKEN_PATH"), Some(&Some(root.join("cache/huggingface/token").to_string_lossy().into_owned())));
    assert!(!root.join("cache/huggingface/token").is_dir());
    assert_eq!(configured.get("PIP_CONFIG_FILE"), Some(&Some("nul".to_owned())));
    assert_eq!(configured.get("PYTHONNOUSERSITE"), Some(&Some("1".to_owned())));
    assert!(!configured.contains_key("HOME"));
    assert_eq!(std::env::var_os("USERPROFILE"), original_profile);
    std::fs::remove_dir_all(root).unwrap();
}
