fn main() {
    let manifest = tauri_build::AppManifest::new().commands(&[
        "pick_video",
        "pick_directory",
        "media_url",
        "engine_request",
        "cancel_engine",
    ]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("failed to build Tauri resources");
}
