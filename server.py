import http.server
import socket
from pathlib import Path
import json
import secrets
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import yt_dlp
import shutil
import urllib.request
import traceback

from lib.convert_bmi import convert_main


PORT = 4334
VIDEO_ROOT = Path("videos")
JOBS_ROOT = Path("jobs")

VIDEO_ROOT.mkdir(exist_ok=True)
JOBS_ROOT.mkdir(exist_ok=True)

executor = ThreadPoolExecutor(max_workers=4)


def is_valid_url(url):
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")
    except:
        return False


def write_json(path, data):
    path.write_text(json.dumps(data))


def download_video(url, filename, cookies=None):
    filename = Path(filename)

    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': str(filename.with_suffix('')) + '.%(ext)s',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'ignoreerrors': True,
        'age_limit': None,
        'quiet': False,
        'no_warnings': True
    }

    if cookies:
        ydl_opts['cookiefile'] = cookies

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        print("Download completed successfully!")
    except Exception as e:
        print("Error:", e)


def background_conversion(video_id, url, width, height):
    job_dir = JOBS_ROOT / video_id
    job_dir.mkdir(parents=True, exist_ok=True)

    status_file = job_dir / "status.json"
    write_json(status_file, {"status": "processing"})

    downloaded_file = None

    try:
        if any(x in url.lower() for x in ['youtube.com', 'youtu.be', 'youtube-nocookie.com']):
            downloaded_file = job_dir / "input.mp4"
            download_video(url, downloaded_file)
        else:
            downloaded_file = job_dir / "input.mp4"
            urllib.request.urlretrieve(url, downloaded_file)

        convert_main(
            str(downloaded_file),
            video_id=video_id,
            width=width,
            height=height
        )

        write_json(status_file, {"status": "done"})

    except Exception as e:
        print(f"Error processing {video_id}: {e}")
        traceback.print_exc()
        write_json(status_file, {"status": "error", "error": str(e)})

    finally:
        # Supprimer la vidéo source téléchargée
        if downloaded_file and Path(downloaded_file).exists():
            Path(downloaded_file).unlink()
            print(f"Deleted: {downloaded_file}")

        # Chemin absolu basé sur l'emplacement de server.py
        server_dir = Path(__file__).parent
        temp_video = server_dir / f"temp_{video_id}.mp4"
        print(f"Looking for temp file: {temp_video}")
        if temp_video.exists():
            temp_video.unlink()
            print(f"Deleted: {temp_video}")
        else:
            print(f"Temp file not found: {temp_video}")
            # Cherche tous les fichiers temp_ pour voir ce qui existe
            for f in server_dir.glob("temp_*.mp4"):
                print(f"Found temp file: {f}")


def background_conversion_from_file(video_id, saved_file, width, height):
    job_dir = JOBS_ROOT / video_id
    job_dir.mkdir(parents=True, exist_ok=True)

    status_file = job_dir / "status.json"
    write_json(status_file, {"status": "processing"})

    try:
        convert_main(
            str(saved_file),
            video_id=video_id,
            width=width,
            height=height
        )
        write_json(status_file, {"status": "done"})

    except Exception as e:
        print(f"Error processing {video_id}: {e}")
        traceback.print_exc()
        write_json(status_file, {"status": "error", "error": str(e)})

    finally:
        if saved_file and Path(saved_file).exists():
            Path(saved_file).unlink()
            print(f"Deleted: {saved_file}")


def parse_multipart(rfile, content_type, content_length):
    """Minimal multipart/form-data parser (no cgi module, removed in Python 3.13+).
    Returns dict: field_name -> {"filename": str|None, "data": bytes}
    Supports only one value per field (enough for this simple upload form)."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
            break
    if not boundary:
        raise ValueError("No boundary found in Content-Type header")

    raw = rfile.read(content_length)
    boundary_bytes = ("--" + boundary).encode()

    fields = {}
    segments = raw.split(boundary_bytes)
    for segment in segments:
        segment = segment.strip(b"\r\n")
        if not segment or segment == b"--":
            continue
        if b"\r\n\r\n" not in segment:
            continue
        headers_blob, body = segment.split(b"\r\n\r\n", 1)
        body = body.rstrip(b"\r\n")

        headers_text = headers_blob.decode("utf-8", errors="replace")
        disposition_line = next(
            (l for l in headers_text.split("\r\n") if l.lower().startswith("content-disposition")),
            None
        )
        if not disposition_line:
            continue

        name = None
        filename = None
        for piece in disposition_line.split(";"):
            piece = piece.strip()
            if piece.startswith("name="):
                name = piece[len("name="):].strip('"')
            elif piece.startswith("filename="):
                filename = piece[len("filename="):].strip('"')

        if name:
            fields[name] = {"filename": filename, "data": body}

    return fields


def search_youtube_videos(query, max_results=6):
    videos_search = VideosSearch(query, limit=max_results)
    results = videos_search.result()["result"]

    videos = []

    for video in results:
        videos.append({
            "title": video["title"],
            "url": video["link"]
        })

    return videos


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed_path = urlparse(self.path)

        if parsed_path.path in ("/", "/index.html"):
            self.serve_index()
        elif parsed_path.path == "/api/videos":
            self.serve_api_videos()
        elif parsed_path.path == "/search":
            self.serve_search(parsed_path)
        elif parsed_path.path.startswith("/videos/"):
            super().do_GET()
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        if self.path == "/download":
            self.handle_download()
        elif self.path == "/upload":
            self.handle_upload()
        else:
            self.send_error(404, "Not Found")

    def serve_search(self, parsed_path):
        try:
            query_params = parse_qs(parsed_path.query)
            q = query_params.get("q", [""])[0]

            if not q:
                self.send_error(400, "Missing 'q' query parameter")
                return

            videos = search_youtube_videos(q, max_results=6)

            data = json.dumps(videos).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self.send_error(500, str(e))

    def serve_index(self):
        try:
            content = Path("index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))

    def serve_api_videos(self):
        videos = []
        for vid in sorted(VIDEO_ROOT.iterdir()):
            preview = vid / "preview.jpg"
            if vid.is_dir() and preview.exists():
                videos.append({
                    "id": vid.name,
                    "preview": f"/videos/{vid.name}/preview.jpg"
                })

        data = json.dumps(videos).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_download(self):
        try:
            if "application/json" not in self.headers.get("Content-Type", ""):
                self.send_error(400, "Content-Type must be application/json")
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))

            url = data.get("url")
            width = data.get("width")
            height = data.get("height")

            if not url or not is_valid_url(url):
                self.send_error(400, "Invalid or missing URL")
                return

            video_id = secrets.token_hex(5)

            response = {"status": "ok", "id": video_id}
            response_bytes = json.dumps(response).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

            executor.submit(
                background_conversion,
                video_id,
                url,
                width,
                height
            )

        except Exception as e:
            self.send_error(500, str(e))

    def handle_upload(self):
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_error(400, "Content-Type must be multipart/form-data")
                return

            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_error(400, "Empty request body")
                return

            fields = parse_multipart(self.rfile, content_type, content_length)

            video_field = fields.get("video")
            if not video_field or not video_field.get("filename"):
                self.send_error(400, "Missing 'video' file field")
                return

            video_id = secrets.token_hex(5)
            job_dir = JOBS_ROOT / video_id
            job_dir.mkdir(parents=True, exist_ok=True)
            saved_file = job_dir / "upload_input.mp4"
            saved_file.write_bytes(video_field["data"])

            response = {"status": "ok", "id": video_id}
            response_bytes = json.dumps(response).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

            width = fields.get("width", {}).get("data")
            height = fields.get("height", {}).get("data")
            width = int(width.decode()) if width else 230
            height = int(height.decode()) if height else 100

            executor.submit(
                background_conversion_from_file,
                video_id,
                saved_file,
                width,
                height
            )

        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))


class IPv6ThreadingServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


if __name__ == "__main__":
    with IPv6ThreadingServer(('::', PORT), Handler) as httpd:
        print(f"IPv6 server running on [::]:{PORT}")
        print(f"Local: http://[::1]:{PORT}")
        print(f"Public: http://[your-ipv6]:{PORT}")
        httpd.serve_forever()
