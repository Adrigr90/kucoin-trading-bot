"""Merkle Tree Hash (RFC 6962 §2.1) and acceptance test A5.

A5: two independent implementations (Python hashlib and OpenSSL) give the RC14 values.
A third reference, the published test vectors of the transparency-dev project, checks the
tree structure: https://github.com/transparency-dev/merkle/blob/main/testonly/constants.go
(Apache License 2.0), consulted on 6 November 2026.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from kbot.core.merkle import empty_root, leaf_hash, node_hash, root

# RC14 in spec 02 §15 gives these values abbreviated: e3b0c442…b855 and 6e340b9c…a01d.
RC14_EMPTY_ROOT = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
RC14_EMPTY_LEAF = "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"

REFERENCE_LEAVES = [
    bytes.fromhex(""),
    bytes.fromhex("00"),
    bytes.fromhex("10"),
    bytes.fromhex("2021"),
    bytes.fromhex("3031"),
    bytes.fromhex("40414243"),
    bytes.fromhex("5051525354555657"),
    bytes.fromhex("606162636465666768696a6b6c6d6e6f"),
]
REFERENCE_ROOTS = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


@pytest.fixture(scope="module")
def openssl_sha256(find_tool: Callable[[str, str | None], str]) -> Callable[[bytes], bytes]:
    binary = find_tool("openssl", "KBOT_OPENSSL")

    def digest(data: bytes) -> bytes:
        result = subprocess.run(
            [binary, "dgst", "-sha256", "-binary"],
            input=data,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return result.stdout

    return digest


def test_rc14_with_hashlib() -> None:
    assert empty_root().hex() == RC14_EMPTY_ROOT
    assert leaf_hash(b"").hex() == RC14_EMPTY_LEAF
    assert RC14_EMPTY_ROOT.startswith("e3b0c442") and RC14_EMPTY_ROOT.endswith("b855")
    assert RC14_EMPTY_LEAF.startswith("6e340b9c") and RC14_EMPTY_LEAF.endswith("a01d")


@pytest.mark.tools
def test_rc14_with_openssl(openssl_sha256: Callable[[bytes], bytes]) -> None:
    assert openssl_sha256(b"").hex() == RC14_EMPTY_ROOT
    assert openssl_sha256(b"\x00").hex() == RC14_EMPTY_LEAF


@pytest.mark.parametrize("size", range(len(REFERENCE_ROOTS)))
def test_reference_roots_with_hashlib(size: int) -> None:
    assert root(REFERENCE_LEAVES[:size]).hex() == REFERENCE_ROOTS[size]


@pytest.mark.tools
@pytest.mark.parametrize("size", range(len(REFERENCE_ROOTS)))
def test_reference_roots_with_openssl(size: int, openssl_sha256: Callable[[bytes], bytes]) -> None:
    assert _openssl_root(REFERENCE_LEAVES[:size], openssl_sha256).hex() == REFERENCE_ROOTS[size]


def test_node_hash_prefix_and_input_types() -> None:
    left, right = leaf_hash(b"a"), leaf_hash(b"b")
    assert node_hash(left, right) == root([b"a", b"b"])
    with pytest.raises(TypeError):
        leaf_hash("text")  # type: ignore[arg-type]


def _openssl_root(leaves: list[bytes], sha256: Callable[[bytes], bytes]) -> bytes:
    """Iterative Merkle Tree Hash, written independently of kbot.core.merkle.

    Leaves enter a stack of complete subtrees whose sizes are powers of two; equal sizes
    merge, and the remaining subtrees fold from the right, as RFC 6962 §2.1 implies.
    """
    if not leaves:
        return sha256(b"")
    stack: list[tuple[int, bytes]] = []
    for leaf in leaves:
        size, digest = 1, sha256(b"\x00" + leaf)
        while stack and stack[-1][0] == size:
            left_size, left = stack.pop()
            size, digest = left_size + size, sha256(b"\x01" + left + digest)
        stack.append((size, digest))
    _, digest = stack.pop()
    while stack:
        _, left = stack.pop()
        digest = sha256(b"\x01" + left + digest)
    return digest
