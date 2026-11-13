use crate::{LocalFiles, canonical_local_file};
use std::{
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};
use tauri::{
    AppHandle, Manager, UriSchemeContext, UriSchemeResponder, WebviewWindow, Wry,
    http::{Method, Request, Response, StatusCode, header},
};

// Tauri's default asset scope grows on every native drop, including directory drops.
// This separate protocol accepts only registered media IDs and never interprets a URL as a path.
pub const PROTOCOL: &str = "dubbing-media";
const MAX_RESPONSE_BYTES: u64 = 8 * 1024 * 1024;

#[tauri::command]
pub async fn media_url(window: WebviewWindow, path: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = canonical_local_file(Path::new(&path)).ok_or("Không tìm thấy tệp xem trước.")?;
        let files = window.state::<LocalFiles>();
        let is_source = files.sources.lock().map_err(|_| "Không thể đọc danh sách video.")?.contains(&path);
        if !is_source
            && !files.generated_media.lock().map_err(|_| "Không thể đọc danh sách xem trước.")?.contains(&path)
        {
            return Err("Tệp chưa được cấp quyền xem trước.".to_owned());
        }

        let mut urls = files.media_urls.lock().map_err(|_| "Không thể tạo đường dẫn xem trước.")?;
        let id = urls.iter().position(|existing| existing == &path).unwrap_or_else(|| {
            urls.push(path);
            urls.len() - 1
        });
        window.convert_file_src(id.to_string(), Some(PROTOCOL)).map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn serve(ctx: UriSchemeContext<'_, Wry>, request: Request<Vec<u8>>, responder: UriSchemeResponder) {
    let path = if ctx.webview_label() == "main" {
        let app: &AppHandle = ctx.app_handle();
        let files = app.state::<LocalFiles>();
        request.uri().path().trim_start_matches('/').parse::<usize>().ok()
            .and_then(|id| files.media_urls.lock().ok()?.get(id).cloned())
    } else {
        None
    };
    tauri::async_runtime::spawn_blocking(move || responder.respond(read_media(path, &request)));
}

fn read_media(path: Option<PathBuf>, request: &Request<Vec<u8>>) -> Response<Vec<u8>> {
    let Some(path) = path else { return empty(StatusCode::NOT_FOUND); };
    if request.method() != Method::GET && request.method() != Method::HEAD {
        return empty(StatusCode::METHOD_NOT_ALLOWED);
    }
    if canonical_local_file(&path).as_ref() != Some(&path) {
        return empty(StatusCode::NOT_FOUND);
    }
    let Ok(mut file) = File::open(&path) else { return empty(StatusCode::NOT_FOUND); };
    let Ok(metadata) = file.metadata() else { return empty(StatusCode::NOT_FOUND); };
    let size = metadata.len();
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or_default();
    let mime = match extension.to_ascii_lowercase().as_str() {
        "mp4" => "video/mp4",
        "mkv" => "video/x-matroska",
        "mov" => "video/quicktime",
        "wav" => "audio/wav",
        _ => return empty(StatusCode::UNSUPPORTED_MEDIA_TYPE),
    };

    let mut response = Response::builder()
        .header(header::CONTENT_TYPE, mime)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::X_CONTENT_TYPE_OPTIONS, "nosniff");
    if request.method() == Method::HEAD {
        return response.header(header::CONTENT_LENGTH, size).body(Vec::new()).unwrap();
    }

    let (start, length) = if let Some(value) = request.headers().get(header::RANGE) {
        let range = value.to_str().ok().and_then(|raw| byte_range(raw, size));
        let Some((start, end)) = range else {
            return response.status(StatusCode::RANGE_NOT_SATISFIABLE)
                .header(header::CONTENT_RANGE, format!("bytes */{size}"))
                .body(Vec::new()).unwrap();
        };
        let end = end.min(start.saturating_add(MAX_RESPONSE_BYTES - 1));
        response = response.status(StatusCode::PARTIAL_CONTENT)
            .header(header::CONTENT_RANGE, format!("bytes {start}-{end}/{size}"));
        (start, end - start + 1)
    } else if size > MAX_RESPONSE_BYTES {
        response = response.status(StatusCode::PARTIAL_CONTENT)
            .header(header::CONTENT_RANGE, format!("bytes 0-{}/{size}", MAX_RESPONSE_BYTES - 1));
        (0, MAX_RESPONSE_BYTES)
    } else {
        (0, size)
    };
    let mut data = vec![0; length as usize];
    if file.seek(SeekFrom::Start(start)).and_then(|_| file.read_exact(&mut data)).is_err() {
        return empty(StatusCode::INTERNAL_SERVER_ERROR);
    }
    response.header(header::CONTENT_LENGTH, length).body(data).unwrap()
}

fn empty(status: StatusCode) -> Response<Vec<u8>> {
    Response::builder().status(status).body(Vec::new()).unwrap()
}

pub fn byte_range(raw: &str, size: u64) -> Option<(u64, u64)> {
    let last = size.checked_sub(1)?;
    let (start, end) = raw.strip_prefix("bytes=")?.split_once('-')?;
    if start.is_empty() {
        let suffix = end.parse::<u64>().ok()?;
        return (suffix > 0).then_some((size.saturating_sub(suffix), last));
    }
    let start = start.parse::<u64>().ok()?;
    let end = if end.is_empty() { last } else { end.parse::<u64>().ok()?.min(last) };
    (start <= end).then_some((start, end))
}
