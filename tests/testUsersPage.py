#!/usr/bin/python
"""`/admin/users` lists each user under its own role's tab and under "all".

The four role tabs read one grouping of the user list, made once in the view (#199).
A role outside KNOWN_ROLES - which nothing should write since #95 - shows under "all"
only. Every tab URL renders all five panes; the URL only picks the active one, so the
grouping has to be there on a tab URL too.
"""

import logging
import re

import pytest

from mcritweb.views.authentication import KNOWN_ROLES

LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
logging.disable(logging.CRITICAL)

#: pane id in users.html -> the role it lists
PANE_ROLE = {"pending": "pending", "visitors": "visitor", "contributors": "contributor", "admins": "admin"}


def rows_per_pane(html):
    panes = re.split(r'<div class="tab-pane', html)[1:]
    return {re.search(r'id="pills-(\w+)"', pane).group(1): [int(i) for i in re.findall(r'scope="row">(\d+)</th>', pane)]
            for pane in panes}


@pytest.mark.parametrize("path", ["/admin/users/", "/admin/users/pending"])
def test_every_user_is_listed_under_its_own_role_and_under_all(client, as_role, make_user, path):
    role_of = {as_role("admin"): "admin"}
    for role in (*KNOWN_ROLES, "blocked"):
        role_of[make_user(role=role, username=f"{role}_user")] = role
    response = client.get(path)
    assert response.status_code == 200
    panes = rows_per_pane(response.get_data(as_text=True))
    assert panes["all"] == sorted(role_of)
    for pane, role in PANE_ROLE.items():
        assert panes[pane] == [user_id for user_id, user_role in sorted(role_of.items()) if user_role == role]
