"""Small class-1 WebDAV backup endpoint. No locks, plugins or vault writes here."""

import asyncio
import errno
import os
import shutil
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from fastapi import Request
from starlette.responses import FileResponse, Response

from core.platforms import is_link, windows_component

DAV = "{DAV:}"
METHODS = "OPTIONS, PROPFIND, MKCOL, PUT, GET, HEAD, DELETE, MOVE, COPY"
PROPERTIES = ("getcontentlength", "getlastmodified", "resourcetype", "getetag", "creationdate")
ET.register_namespace("d", "DAV:")


class DAVError(Exception):
    def __init__(self, status):
        self.status = status


def dav_path(root, relative):
    """Already URL-decoded, relative path. Reject links in every component."""
    if (
        relative.startswith("/")
        or any(c in relative for c in "\\:")
        or any(unicodedata.category(c).startswith("C") for c in relative)
        or any(p in {".", ".."} for p in relative.split("/"))
    ):
        raise DAVError(403)
    target = root
    if is_link(target):
        raise DAVError(403)
    for part in relative.split("/"):
        if not part:
            continue
        if os.name == "nt" and not windows_component(part):
            raise DAVError(403)
        target /= part
        if is_link(target):
            raise DAVError(403)
    if not target.resolve().is_relative_to(root.resolve()):
        raise DAVError(403)
    return target


def tree_size(path, reject_links=True):
    """Also validate descendants before recursive COPY / MOVE / DELETE."""
    if is_link(path):
        if reject_links:
            raise DAVError(403)
        return 0
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(tree_size(p, reject_links) for p in path.iterdir())


def etag(path):
    stat = path.stat()
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


class WebDAV:
    def __init__(self, service):
        self.service = service
        self.root = service.settings.data_dir / "dav"
        dav_path(self.root, "goodnotes").mkdir(parents=True, exist_ok=True)
        self.temporary = service.settings.data_dir / "dav_uploads"
        self.temporary.mkdir(exist_ok=True)
        self.lock = asyncio.Lock()

    def install(self, app):
        # Explicit route order before the MCP catch-all. Never included in OpenAPI.
        app.add_api_route(
            "/dav",
            self.handle,
            methods=METHODS.split(", ") + ["LOCK", "UNLOCK"],
            include_in_schema=False,
        )
        app.add_api_route(
            "/dav/{relative:path}",
            self.handle,
            methods=METHODS.split(", ") + ["LOCK", "UNLOCK"],
            include_in_schema=False,
        )

    def target(self, url_path):
        if url_path == "/dav":
            return dav_path(self.root, "")
        if not url_path.startswith("/dav/"):
            raise DAVError(403)
        return dav_path(self.root, url_path[5:])

    def destination(self, request):
        value = request.headers.get("destination", "")
        uri = urlsplit(value)
        if (
            not value
            or uri.query
            or uri.fragment
            or uri.username is not None
            or (uri.scheme and uri.scheme.lower() not in {"http", "https"})
            or (uri.netloc and not self.same_server(uri.netloc, request.headers))
            or (uri.scheme and not uri.netloc)
        ):
            raise DAVError(400 if not value else 502)
        return self.target(unquote(uri.path, errors="strict"))

    def same_server(self, netloc, headers):
        host = self.authority(headers.get("host", ""))
        # tailscale serve may forward with Host 127.0.0.1:8765. The backend listens on
        # loopback only, so a proxied request can only be naming this server.
        if host[0] in {"127.0.0.1", "localhost", "::1"}:
            return True
        names = {host, self.authority(headers.get("x-forwarded-host", ""))}
        return self.authority(netloc) in names

    @staticmethod
    def authority(value):
        uri = urlsplit("//" + value)
        # Serve may terminate HTTPS and forward HTTP; default ports still name the same
        # authority. Non-default ports must match, and a different host is never accepted.
        return (uri.hostname or "").lower(), None if uri.port in {None, 80, 443} else uri.port

    async def body(self, request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 64 * 1024:
                raise DAVError(413)
        return bytes(body)

    async def handle(self, request: Request):
        try:
            # All DAV mutations share a lock, including quota calculation and streamed PUT.
            async with self.lock:
                path = self.target(request.scope["path"])
                return await self.dispatch(request, path)
        except DAVError as exc:
            return Response(status_code=exc.status, headers={"DAV": "1", "Allow": METHODS})
        except (ValueError, UnicodeError, ET.ParseError):
            return Response(status_code=400)
        except OSError as exc:
            status = 507 if exc.errno in {errno.ENOSPC, errno.EDQUOT} else 403
            return Response(status_code=status)

    async def dispatch(self, request, path):
        method = request.method
        if method == "OPTIONS":
            return Response(headers={"DAV": "1", "Allow": METHODS})
        if method in {"LOCK", "UNLOCK"}:
            raise DAVError(405)
        if method == "PROPFIND":
            return await self.propfind(request, path)
        exists = path.exists()
        if method in {"GET", "HEAD"}:
            if not exists:
                raise DAVError(404)
            if not path.is_file():
                raise DAVError(405)
            return FileResponse(path, headers={"ETag": etag(path)})
        if request.headers.get("if-match") and (
            not exists or request.headers["if-match"] not in {"*", etag(path)}
        ):
            raise DAVError(412)
        if exists and request.headers.get("if-none-match") in {"*", etag(path)}:
            raise DAVError(412)
        if method == "MKCOL":
            if exists:
                raise DAVError(405)
            if await self.body(request):
                raise DAVError(415)
            if not path.parent.is_dir():
                raise DAVError(409)
            await asyncio.to_thread(path.mkdir)
            return Response(status_code=201)
        if method == "PUT":
            return await self.put(request, path)
        if not exists:
            raise DAVError(404)
        if path == self.root:
            raise DAVError(403)
        if method == "DELETE":
            await asyncio.to_thread(tree_size, path)
            await asyncio.to_thread(self.remove, path)
            return Response(status_code=204)
        if method in {"MOVE", "COPY"}:
            return await self.transfer(request, path)
        raise DAVError(405)

    async def put(self, request, path):
        if path.is_dir():
            raise DAVError(405)
        if not path.parent.is_dir():
            raise DAVError(409)
        existed = path.exists()
        settings = self.service.store.settings
        used = await asyncio.to_thread(tree_size, self.root, False)
        old_size = path.stat().st_size if existed else 0
        allowance = min(settings.dav_max_file_bytes, settings.dav_max_total_bytes - used + old_size)
        length = request.headers.get("content-length")
        if length and (int(length) < 0 or int(length) > allowance):
            raise DAVError(507)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.temporary, delete=False) as output:
                name = output.name
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > allowance:
                        raise DAVError(507)
                    await asyncio.to_thread(output.write, chunk)
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            # Recheck components after the stream; DAV itself cannot create symlinks.
            dav_path(self.root, path.relative_to(self.root).as_posix())
            await asyncio.to_thread(os.replace, name, path)
            return Response(status_code=204 if existed else 201, headers={"ETag": etag(path)})
        finally:
            if name:
                Path(name).unlink(missing_ok=True)

    @staticmethod
    def remove(path):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    async def transfer(self, request, source):
        destination = self.destination(request)
        overwrite = request.headers.get("overwrite", "T")
        if overwrite not in {"T", "F"}:
            raise DAVError(400)
        if (
            destination == source
            or destination == self.root
            or destination.is_relative_to(source)
            or source.is_relative_to(destination)
        ):
            raise DAVError(403)
        existed = destination.exists()
        if existed and overwrite == "F":
            raise DAVError(412)
        if not destination.parent.is_dir():
            raise DAVError(409)
        depth = request.headers.get("depth", "infinity")
        if source.is_dir() and depth not in (
            {"infinity"} if request.method == "MOVE" else {"0", "infinity"}
        ):
            raise DAVError(400)
        size = await asyncio.to_thread(tree_size, source)
        replaced = await asyncio.to_thread(tree_size, destination)
        if request.method == "COPY":
            settings = self.service.store.settings
            size = 0 if source.is_dir() and depth == "0" else size
            used = await asyncio.to_thread(tree_size, self.root, False)
            files = (
                ([] if depth == "0" else list(source.rglob("*"))) if source.is_dir() else [source]
            )
            if used - replaced + size > settings.dav_max_total_bytes or any(
                p.is_file() and p.stat().st_size > settings.dav_max_file_bytes for p in files
            ):
                raise DAVError(507)
        # Stage COPY outside the public namespace; an interrupted copy leaves the old target.
        with tempfile.TemporaryDirectory(dir=self.temporary) as folder:
            stage = Path(folder) / "copy"
            if request.method == "COPY":
                if source.is_dir():
                    if depth == "0":
                        stage.mkdir()
                    else:
                        await asyncio.to_thread(shutil.copytree, source, stage)
                else:
                    await asyncio.to_thread(shutil.copyfile, source, stage)
            incoming = source if request.method == "MOVE" else stage
            if existed and destination.is_file() and incoming.is_file():
                await asyncio.to_thread(os.replace, incoming, destination)
            else:
                previous = Path(folder) / "previous"
                if existed:
                    await asyncio.to_thread(os.replace, destination, previous)
                try:
                    await asyncio.to_thread(os.replace, incoming, destination)
                except BaseException:
                    if previous.exists():
                        os.replace(previous, destination)
                    raise
        return Response(status_code=204 if existed else 201)

    async def propfind(self, request, path):
        if not path.exists():
            raise DAVError(404)
        depth = request.headers.get("depth", "infinity")
        if depth not in {"0", "1"}:
            return Response(
                '<d:error xmlns:d="DAV:"><d:propfind-finite-depth/></d:error>',
                403,
                media_type="application/xml",
            )
        body = await self.body(request)
        mode, requested = "allprop", []
        if body.strip():
            if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                raise DAVError(400)
            root = ET.fromstring(body)
            if root.tag != DAV + "propfind" or len(root) != 1:
                raise DAVError(400)
            mode = root[0].tag.removeprefix(DAV)
            if mode not in {"allprop", "propname", "prop"}:
                raise DAVError(400)
            requested = [p.tag for p in root[0]]
        paths = [path]
        if depth == "1" and path.is_dir():
            paths.extend(p for p in sorted(path.iterdir()) if not is_link(p))
        root = ET.Element(DAV + "multistatus")
        for target in paths:
            stat = target.stat()
            response = ET.SubElement(root, DAV + "response")
            relative = target.relative_to(self.root).as_posix()
            href = "/dav/" + ("" if relative == "." else quote(relative, safe="/"))
            if target.is_dir() and not href.endswith("/"):
                href += "/"
            ET.SubElement(response, DAV + "href").text = href
            values = {
                "getcontentlength": str(0 if target.is_dir() else stat.st_size),
                "getlastmodified": formatdate(stat.st_mtime, usegmt=True),
                "getetag": etag(target),
                "creationdate": datetime.fromtimestamp(
                    getattr(stat, "st_birthtime", stat.st_ctime), UTC
                ).isoformat(),
                "resourcetype": None,
            }
            names = requested if mode == "prop" else [DAV + p for p in PROPERTIES]
            for supported in (True, False):
                selected = [n for n in names if (n in {DAV + p for p in PROPERTIES}) == supported]
                if not selected:
                    continue
                propstat = ET.SubElement(response, DAV + "propstat")
                props = ET.SubElement(propstat, DAV + "prop")
                for name in selected:
                    element = ET.SubElement(props, name)
                    if supported and mode != "propname":
                        element.text = values[name.removeprefix(DAV)]
                        if name == DAV + "resourcetype" and target.is_dir():
                            ET.SubElement(element, DAV + "collection")
                ET.SubElement(propstat, DAV + "status").text = (
                    "HTTP/1.1 200 OK" if supported else "HTTP/1.1 404 Not Found"
                )
        return Response(
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
            207,
            media_type="application/xml",
            headers={"DAV": "1"},
        )
