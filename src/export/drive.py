from __future__ import annotations

import logging
import mimetypes
from io import BytesIO

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


class DriveClient:
    """Google Drive uploader. Uses Application Default Credentials.

    With ADC + drive.file scope, this client can only see / manage files
    created by itself. It cannot read your existing Drive contents.
    """

    def __init__(self) -> None:
        creds, _ = google.auth.default(scopes=DRIVE_SCOPES)
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def create_folder(self, name: str, parent_id: str | None = None) -> str:
        body: dict = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]
        folder = self._service.files().create(body=body, fields="id").execute()
        return folder["id"]

    def find_folder(self, name: str, parent_id: str | None = None) -> str | None:
        """Find a folder by name among app-created files. Returns ID or None."""
        escaped = name.replace("'", "\\'")
        q_parts = [
            f"name = '{escaped}'",
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_id:
            q_parts.append(f"'{parent_id}' in parents")
        else:
            q_parts.append("'root' in parents")
        result = (
            self._service.files()
            .list(q=" and ".join(q_parts), fields="files(id, name)", pageSize=1)
            .execute()
        )
        files = result.get("files", [])
        return files[0]["id"] if files else None

    def find_or_create_folder(self, name: str, parent_id: str | None = None) -> str:
        existing = self.find_folder(name, parent_id)
        if existing:
            return existing
        return self.create_folder(name, parent_id)

    def upload_file(
        self,
        name: str,
        data: bytes,
        parent_id: str,
        mime_type: str | None = None,
    ) -> str:
        if not mime_type:
            mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

        media = MediaIoBaseUpload(BytesIO(data), mimetype=mime_type, resumable=False)
        body = {"name": name, "parents": [parent_id]}
        file = (
            self._service.files()
            .create(body=body, media_body=media, fields="id")
            .execute()
        )
        return file["id"]

    def make_shareable(self, file_or_folder_id: str) -> str:
        """Set 'anyone with link can view' and return the shareable URL."""
        self._service.permissions().create(
            fileId=file_or_folder_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
        return f"https://drive.google.com/drive/folders/{file_or_folder_id}"
