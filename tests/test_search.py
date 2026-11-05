"""The search box's name parsing: TNS names in their usual spellings, and what isn't one."""

import pytest

from dozynki import search


@pytest.mark.parametrize("q, name", [
    ("SN 2025abc", "2025abc"), ("AT2024ggi", "2024ggi"), ("2024GGI", "2024ggi"), ("TDE 2023abcd", "2023abcd"),
    (" at_2026buh ", "2026buh"),
])
def test_tns_names(q, name):
    assert search.tns_name(q) == name


@pytest.mark.parametrize("q", ["313871014435488133", "ANT2026abc", "ANT20213giqq", "lsst:12345678", "12.3 -4.5", "ZTF24aaabcde"])
def test_not_tns_names(q):
    assert search.tns_name(q) is None
