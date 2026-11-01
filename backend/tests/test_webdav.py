"""In-process DAV wire tests, including streaming failures and hostile paths."""

import base64
import xml.etree.ElementTree as ET

import httpx
import pytest

from core.api import create_app
from core.models import RuntimeSettings


@pytest.fixture
async def dav(service):
    token, _ = service.tokens.create("GoodNotes test", ["dav"])
    app = create_app(service=service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        yield client


async def test_dav_methods_and_properties(dav, service):
    root = "/dav/goodnotes/"
    options = await dav.options(root)
    assert options.headers["dav"] == "1" and "LOCK" not in options.headers["allow"]
    assert (await dav.request("MKCOL", root + "课程")).status_code == 201
    file = root + "课程/第5章.pdf"
    assert (await dav.put(file, content=b"pdf")).status_code == 201
    for depth, count in (("0", 1), ("1", 2)):
        response = await dav.request("PROPFIND", root + "课程/", headers={"Depth": depth})
        assert response.status_code == 207
        xml = ET.fromstring(response.content)
        assert len(xml.findall("{DAV:}response")) == count
        for entry in xml:
            prop = entry.find("{DAV:}propstat/{DAV:}prop")
            assert {p.tag for p in prop} == {
                "{DAV:}" + n
                for n in (
                    "getcontentlength",
                    "getlastmodified",
                    "resourcetype",
                    "getetag",
                    "creationdate",
                )
            }
            assert entry.find("{DAV:}href").text.startswith("/dav/")
    assert (await dav.request("PROPFIND", root, headers={"Depth": "infinity"})).status_code == 403
    assert (await dav.get(file)).content == b"pdf"
    head = await dav.head(file)
    assert head.status_code == 200 and head.content == b"" and head.headers["content-length"] == "3"
    assert (
        await dav.request(
            "COPY", file, headers={"Destination": "http://test/dav/goodnotes/copy.pdf"}
        )
    ).status_code == 201
    assert (
        await dav.request(
            "MOVE", root + "copy.pdf", headers={"Destination": "/dav/goodnotes/moved.pdf"}
        )
    ).status_code == 201
    assert (await dav.get(root + "copy.pdf")).status_code == 404
    assert (
        await dav.request(
            "COPY", file, headers={"Destination": root + "moved.pdf", "Overwrite": "F"}
        )
    ).status_code == 412
    assert (
        await dav.request(
            "MOVE", file, headers={"Destination": root + "moved.pdf", "Overwrite": "F"}
        )
    ).status_code == 412
    assert (
        await dav.request("MOVE", file, headers={"Destination": root + "moved.pdf"})
    ).status_code == 204
    assert (await dav.delete(root + "课程/")).status_code == 204
    assert (await dav.delete(root + "moved.pdf")).status_code == 204
    assert not list((service.settings.data_dir / "dav_uploads").iterdir())
    for method in ("LOCK", "UNLOCK"):
        assert (await dav.request(method, root)).status_code == 405


async def test_propfind_requested_properties(dav):
    response = await dav.request(
        "PROPFIND",
        "/dav/",
        headers={"Depth": "0"},
        content='<d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:unknown/></d:prop></d:propfind>',
    )
    root = ET.fromstring(response.content)
    assert [s.text for s in root.findall(".//{DAV:}status")] == [
        "HTTP/1.1 200 OK",
        "HTTP/1.1 404 Not Found",
    ]
    response = await dav.request(
        "PROPFIND",
        "/dav/",
        headers={"Depth": "0"},
        content='<d:propfind xmlns:d="DAV:"><d:propname/></d:propfind>',
    )
    assert response.status_code == 207
    assert all(p.text is None for p in ET.fromstring(response.content).findall(".//{DAV:}prop/*"))


async def test_basic_bearer_and_scope_isolation(service):
    token, _ = service.tokens.create("DAV", ["dav"])
    app_token, _ = service.tokens.create("App", ["app"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service=service)), base_url="http://test"
    ) as client:
        assert (await client.get("/dav/goodnotes/")).status_code == 401
        for scheme in (
            "Bearer " + token,
            "Basic " + base64.b64encode(("anything:" + token).encode()).decode(),
        ):
            assert (
                await client.options("/dav/", headers={"Authorization": scheme})
            ).status_code == 200
        assert (
            await client.get(
                "/api/v1/knowledge/inputs", headers={"Authorization": "Bearer " + token}
            )
        ).status_code == 403
        assert (
            await client.get("/openapi.json", headers={"Authorization": "Bearer " + token})
        ).status_code == 403
        assert (
            await client.get(
                "/api/v1/status",
                headers={
                    "Authorization": "Basic "
                    + base64.b64encode(("x:" + app_token).encode()).decode()
                },
            )
        ).status_code == 401
        assert (
            await client.options("/dav/", headers={"Authorization": "Bearer " + app_token})
        ).status_code == 200
        assert (
            await client.options("/dav/", headers={"Authorization": "Basic !!!"})
        ).status_code == 401
        service.tokens.revoke("DAV")
        for scheme in ("Bearer ", "Basic "):
            value = scheme + (
                token
                if scheme.startswith("Bearer")
                else base64.b64encode(("x:" + token).encode()).decode()
            )
            assert (
                await client.options("/dav/", headers={"Authorization": value})
            ).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/escape",
        "nested/%2e%2e/file",
        "%2Fetc/passwd",
        "bad%00name",
        "bad%0Aname",
        "bad%7Fname",
        "C:%5Cescape",
        "%5Cescape",
    ],
)
async def test_invalid_paths(dav, path):
    assert (await dav.put("/dav/" + path, content=b"bad")).status_code == 403


async def test_symlinks_and_destinations(dav, service, tmp_path):
    root = service.settings.data_dir / "dav"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_bytes(b"secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    for method in ("PUT", "GET", "HEAD", "DELETE", "PROPFIND", "MKCOL"):
        assert (
            await dav.request(method, "/dav/link/private", headers={"Depth": "0"})
        ).status_code == 403
    await dav.put("/dav/goodnotes/ok.pdf", content=b"good")
    for dest, code in (
        ("http://evil/dav/else", 502),
        ("/api/v1/status", 403),
        ("/dav/%2e%2e/escape", 403),
        ("/dav/link/private", 403),
    ):
        assert (
            await dav.request("COPY", "/dav/goodnotes/ok.pdf", headers={"Destination": dest})
        ).status_code == code
    assert (outside / "private").read_bytes() == b"secret"
    assert (await dav.delete("/dav")).status_code == 403


async def test_streamed_limits_atomic_replace_and_quota(dav, service):
    service.db.put(
        "settings", RuntimeSettings(dav_max_file_bytes=5, dav_max_total_bytes=8).model_dump()
    )
    url = "/dav/goodnotes/a.pdf"
    await dav.put(url, content=b"12345")

    async def chunks():
        for chunk in (b"12", b"34", b"56"):
            yield chunk

    assert (await dav.put(url, content=chunks())).status_code == 507
    assert (await dav.get(url)).content == b"12345"
    assert (await dav.put("/dav/goodnotes/b.pdf", content=b"1234")).status_code == 507
    assert (await dav.put(url, content=b"12")).status_code == 204
    assert (await dav.put("/dav/goodnotes/b.pdf", content=b"12345")).status_code == 201
    assert (
        await dav.request("COPY", url, headers={"Destination": "/dav/goodnotes/c.pdf"})
    ).status_code == 507
    assert not list((service.settings.data_dir / "dav_uploads").iterdir())


async def test_collections_copy_move_depth_and_overwrite(dav):
    for name in ("a", "a/nested", "b"):
        assert (await dav.request("MKCOL", "/dav/" + name)).status_code == 201
    await dav.put("/dav/a/nested/file", content=b"value")
    assert (
        await dav.request("COPY", "/dav/a", headers={"Destination": "/dav/c", "Depth": "0"})
    ).status_code == 201
    assert (await dav.get("/dav/c/nested/file")).status_code == 404
    assert (
        await dav.request("COPY", "/dav/a", headers={"Destination": "/dav/b"})
    ).status_code == 204
    assert (await dav.get("/dav/b/nested/file")).content == b"value"
    assert (
        await dav.request("MOVE", "/dav/a", headers={"Destination": "/dav/d"})
    ).status_code == 201
    assert (await dav.get("/dav/d/nested/file")).content == b"value"
    assert (
        await dav.request("COPY", "/dav/d", headers={"Destination": "/dav/d/sub"})
    ).status_code == 403


async def test_dav_bypasses_api_body_cap(dav):
    # Chunked >50 MiB request proves the middleware doesn't buffer/apply the App cap.
    async def chunks():
        for _ in range(51):
            yield b"p" * 1024 * 1024

    assert (await dav.put("/dav/goodnotes/large.pdf", content=chunks())).status_code == 201
    assert (
        int((await dav.head("/dav/goodnotes/large.pdf")).headers["content-length"])
        == 51 * 1024 * 1024
    )


async def test_concurrent_puts_share_quota(dav, service):
    import asyncio

    service.db.put(
        "settings", RuntimeSettings(dav_max_file_bytes=6, dav_max_total_bytes=8).model_dump()
    )
    responses = await asyncio.gather(
        dav.put("/dav/a", content=b"123456"), dav.put("/dav/b", content=b"123456")
    )
    assert sorted(r.status_code for r in responses) == [201, 507]


async def test_preconditions_and_https_destination_default_port(dav):
    result = await dav.put("/dav/a", content=b"first")
    etag = result.headers["etag"]
    assert (
        await dav.put("/dav/a", content=b"other", headers={"If-None-Match": "*"})
    ).status_code == 412
    assert (
        await dav.put("/dav/a", content=b"other", headers={"If-Match": '"stale"'})
    ).status_code == 412
    assert (await dav.put("/dav/a", content=b"new", headers={"If-Match": etag})).status_code == 204
    assert (
        await dav.request("COPY", "/dav/a", headers={"Destination": "https://test:443/dav/b"})
    ).status_code == 201
    assert (await dav.get("/dav/b")).content == b"new"


async def test_destination_behind_tailscale_serve(dav):
    await dav.put("/dav/a", content=b"x")
    # tailscale serve may forward with Host 127.0.0.1:8765; the ts.net name still works.
    proxied = {"Host": "127.0.0.1:8765", "Destination": "https://mini.tail.ts.net/dav/b"}
    assert (await dav.request("COPY", "/dav/a", headers=proxied)).status_code == 201
    forwarded = {
        "Host": "backend.internal",
        "X-Forwarded-Host": "mini.tail.ts.net",
        "Destination": "https://mini.tail.ts.net/dav/c",
    }
    assert (await dav.request("COPY", "/dav/a", headers=forwarded)).status_code == 201
    foreign = {"Host": "mini.tail.ts.net", "Destination": "https://elsewhere.example/dav/d"}
    assert (await dav.request("COPY", "/dav/a", headers=foreign)).status_code == 502
