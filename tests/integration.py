#!/usr/bin/env python3
"""Compile HTTP artifacts with installed rockc, then check protocol and TCP behavior."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rockc", default=os.environ.get("ROCKC", "rockc"),
                        help="compiler executable (default: ROCKC or rockc on PATH)")
    parser.add_argument("--sysroot", type=Path,
                        help="optional matching installed toolchain sysroot")
    parser.add_argument("--release", action="store_true",
                        help="compile artifacts and fixtures with optimization")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compiler = [args.rockc]
    if args.sysroot is not None:
        compiler += ["--sysroot", str(args.sysroot.resolve())]
    target_libdir = subprocess.run(
        compiler + ["--print", "target-libdir"],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    std = Path(target_libdir) / "std.rkca"
    if not target_libdir or not std.is_file():
        parser.error(f"installed std artifact not found: {std}")
    compiler += ["--release" if args.release else "--debug",
                 "--extern-artifact", f"std={std}"]

    def compile_rock(source, output, *extra):
        output.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            compiler + ["--entry-file", str(root / source),
                        "--output-dir", str(output), *map(str, extra)],
            cwd=root, check=True, timeout=300,
        )

    with tempfile.TemporaryDirectory(prefix="rock-http-") as temporary:
        build = Path(temporary)
        artifact = build / "http" / "rock_http.rkca"
        compile_rock("src/lib.rk", artifact.parent,
                     "--crate-name", "rock_http", "--no-link",
                     "--emit-object", artifact.with_suffix(".o"),
                     "--emit-artifact", artifact)
        dependency = ["--extern-artifact", f"rock_http={artifact}"]
        compile_rock("examples/protocol_test.rk", build / "protocol", *dependency)
        protocol = subprocess.run([build / "protocol" / "protocol_test"],
                                  cwd=build, capture_output=True, timeout=10)
        expected = (b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                    b"Content-Length: 5\r\nConnection: close\r\n\r\nhello")
        if protocol.returncode != 0 or protocol.stdout != expected:
            raise AssertionError(f"protocol fixture failed: {protocol!r}")

        compile_rock("examples/server.rk", build / "server", *dependency)
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            address = reserved.getsockname()

        # A file avoids blocking the server on a full stderr pipe.
        with tempfile.TemporaryFile() as errors:
            server = subprocess.Popen(
                [build / "server" / "server", str(address[1])], cwd=build,
                stdout=subprocess.DEVNULL, stderr=errors,
            )
            try:
                deadline = time.monotonic() + 5
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(f"server exited before readiness: {server.returncode}")
                    try:
                        with socket.create_connection(address, timeout=1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("server readiness timed out")
                        time.sleep(0.025)

                def exchange(parts):
                    with socket.create_connection(address, timeout=5) as stream:
                        for part in parts:
                            stream.sendall(part)
                        response = bytearray()
                        deadline = time.monotonic() + 5
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError("response did not finish within five seconds")
                            stream.settimeout(remaining)
                            chunk = stream.recv(65536)
                            if not chunk:
                                return bytes(response)
                            response.extend(chunk)

                get = exchange([b"GET / HTTP/1.1\r\nHost: local\r\n\r\n"])
                if not get.endswith(b"hello from Rock"):
                    raise AssertionError(f"unexpected GET response: {get!r}")
                head = exchange([b"HEAD / HTTP/1.1\r\nHost: local\r\n\r\n"])
                if not head.endswith(b"\r\n\r\n") or b"Content-Length: 15\r\n" not in head:
                    raise AssertionError(f"unexpected HEAD response: {head!r}")
                post = exchange([b"POST /echo HTTP/1.1\r\nContent-Len",
                                 b"gth: 5\r\n\r\nhe", b"llo"])
                if not post.endswith(b"hello"):
                    raise AssertionError(f"unexpected fragmented POST response: {post!r}")
                binary_body = b"\x00\xff\x80\r\n" + "Rock 🦀".encode("utf-8")
                binary_head = (f"POST /echo HTTP/1.1\r\nContent-Length: {len(binary_body)}\r\n"
                               "\r\n").encode("ascii")
                binary = exchange([binary_head, binary_body])
                expected_binary = (b"HTTP/1.1 200 OK\r\n"
                                   + f"Content-Length: {len(binary_body)}\r\n".encode("ascii")
                                   + b"Connection: close\r\n\r\n" + binary_body)
                if binary != expected_binary:
                    raise AssertionError(f"unexpected binary response: {binary!r}")
                unicode = exchange([b"GET /unicode HTTP/1.1\r\n\r\n"])
                expected_unicode = (b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n"
                                    b"Connection: close\r\n\r\n" + "Rock 🦀\0".encode("utf-8"))
                if unicode != expected_unicode:
                    raise AssertionError(f"unexpected UTF-8 response: {unicode!r}")

                # Reject untrusted bytes before String::from, and exercise cleanup
                # after headers have already been allocated in previous iterations.
                invalid_requests = [
                    b"G\xffT / HTTP/1.1\r\n\r\n",
                    b"GET /\xff HTTP/1.1\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: local\r\nMissing-Colon\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: local\r\nBad Name: value\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: local\r\nX-Test: \xff\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: local\r\nX-Test: \x00\r\n\r\n",
                    b"GET / HTTP/1.1\r\nHost: local\r\nContent-Length: -1\r\n\r\n",
                    b"GET / HTTP/1.1\r\nContent-Length: 9223372036854775808\r\n\r\n",
                    b"GET / HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n",
                ]
                for request in invalid_requests:
                    rejected = exchange([request])
                    if not rejected.startswith(b"HTTP/1.1 400 "):
                        raise AssertionError(f"unexpected rejection for {request!r}: {rejected!r}")
                too_many = exchange([b"GET / HTTP/1.1\r\n" + b"X-Test: value\r\n" * 101 + b"\r\n"])
                if not too_many.startswith(b"HTTP/1.1 431 "):
                    raise AssertionError(f"unexpected header limit response: {too_many!r}")
                duplicate_length = exchange([
                    b"POST /echo HTTP/1.1\r\nContent-Length: 5\r\nContent-Length: 5\r\n\r\nhello",
                ])
                if not duplicate_length.startswith(b"HTTP/1.1 200 ") or not duplicate_length.endswith(b"hello"):
                    raise AssertionError(f"unexpected duplicate length response: {duplicate_length!r}")
                unsupported = exchange([b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"])
                if not unsupported.startswith(b"HTTP/1.1 501 "):
                    raise AssertionError(f"unexpected transfer response: {unsupported!r}")
                recovered = exchange([b"GET /after-error HTTP/1.1\r\nHost: local\r\n\r\n"])
                if not recovered.endswith(b"hello from Rock"):
                    raise AssertionError(f"server did not recover: {recovered!r}")
                with ThreadPoolExecutor(max_workers=4) as workers:
                    responses = workers.map(exchange, [
                        [b"GET /concurrent HTTP/1.1\r\nHost: local\r\n\r\n"]
                        for _ in range(4)
                    ])
                    for response in responses:
                        if not response.endswith(b"hello from Rock"):
                            raise AssertionError(f"unexpected concurrent response: {response!r}")
            except BaseException:
                errors.seek(0)
                print(errors.read().decode(errors="replace"), end="", file=sys.stderr)
                raise
            finally:
                server.kill()
                server.wait(timeout=5)
    print("HTTP artifact, protocol, and real socket checks passed")


if __name__ == "__main__":
    main()
