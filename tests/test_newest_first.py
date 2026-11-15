"""Regression checks for newest-first searches; no network or real mailbox writes."""
import email.utils
import imaplib
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "upstream" / "server"))
from backend_imap import ImapBackend, ImapError, IndexPendingError
from emailutil import date_sort_key
import server as mailserver


class Connection:
    def __init__(self, messages):
        self.messages = messages
        self.capabilities = (b"IMAP4REV1",)
        self.untagged_responses = {"UIDVALIDITY": [b"1"]}
        self.commands = []
        self.sorted_uids = None
        self.reject_sort = False
        self.reject_fetch = False

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "SORT":
            if self.reject_sort:
                raise imaplib.IMAP4.error("SORT rejected")
            return "OK", [" ".join(map(str, self.sorted_uids)).encode()]
        if command == "FETCH":
            if self.reject_fetch:
                return "NO", [b"not available"]
            selected = [int(uid) for uid in args[0].split(",")]
            result = []
            for uid in sorted(selected):
                if uid not in self.messages:
                    continue
                item = self.messages[uid]
                attachment = ' BODYSTRUCTURE ("ATTACHMENT")' if item.get("attachment") else ""
                meta = (f'{uid} (UID {uid} FLAGS () RFC822.SIZE 100 '
                        f'INTERNALDATE "{item.get("internal", "01-Jan-2020 00:00:00 +0000")}"'
                        + attachment + ')').encode()
                header = ("Date: " + item["date"] + "\r\n\r\n").encode() if item.get("date") else b"\r\n"
                result.append((meta, header))
            return "OK", result
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


def backend(messages):
    result = ImapBackend(SimpleNamespace(name="fixture", imap_host="imap.example.test",
                                       imap_port=993, imap_user="fixture@example.test"))
    connection = Connection(messages)
    result.conn = lambda: connection
    result._resolve_folder = lambda folder: "INBOX"
    result._select = lambda folder, readonly=True: None
    result._uid_search = lambda criteria: sorted(connection.messages)
    return result, connection


class NewestFirst(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)
        self.config_patch = patch('backend_imap.CONFIG_DIR', self.cache.name)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
    def fixture(self):
        return {
            2462: {"date": "Fri, 13 Nov 2026 12:00:00 +0800"},
            3930: {"date": "Mon, 26 Aug 2024 09:04:44 -0700"},
        }

    def test_newest_date_wins_over_larger_uid(self):
        instance, connection = backend(self.fixture())
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|2462")
        date_fetch = [args for command, args in connection.commands if command == "FETCH" and "BODYSTRUCTURE" not in args[1]]
        self.assertEqual(len(date_fetch), 1)
        self.assertEqual(set(date_fetch[0][0].split(",")), {"2462", "3930"})
        self.assertIn("BODY.PEEK", date_fetch[0][1])
        summary_fetch = [args for command, args in connection.commands if command == "FETCH" and "BODYSTRUCTURE" in args[1]]
        self.assertEqual(summary_fetch[0][0], "2462")

    def test_timezone_comparison_uses_utc_instant(self):
        messages = {1: {"date": "Fri, 13 Nov 2026 00:30:00 +1400"},
                    2: {"date": "Thu, 12 Nov 2026 22:30:00 -0800"}}
        instance, _ = backend(messages)
        self.assertEqual([m["id"] for m in instance.search(limit=2)], ["INBOX|2", "INBOX|1"])

    def test_cache_fetches_only_new_uids(self):
        instance, connection = backend(self.fixture())
        instance.search(limit=1)
        connection.commands.clear()
        instance.search(limit=1)
        self.assertFalse(any("BODYSTRUCTURE" not in args[1] for command, args in connection.commands if command == "FETCH"))
        connection.messages[100] = {"date": "Sat, 14 Nov 2026 12:00:00 +0800"}
        connection.commands.clear()
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|100")
        self.assertEqual([args[0] for command, args in connection.commands if command == "FETCH" and "BODYSTRUCTURE" not in args[1]], ["100"])

    def test_uidvalidity_and_reconnect_invalidate_cache(self):
        instance, connection = backend(self.fixture())
        instance.search(limit=1)
        connection.messages[2462], connection.messages[3930] = connection.messages[3930], connection.messages[2462]
        connection.untagged_responses["UIDVALIDITY"] = [b"2"]
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|3930")
        instance._reset()
        self.assertEqual(instance._date_cache, {})

    def test_attachment_filter_continues_past_first_window(self):
        messages = {uid: {"date": f"{14-uid:02d} Nov 2026 12:00:00 +0000", "attachment": uid == 8}
                    for uid in range(1, 11)}
        instance, _ = backend(messages)
        self.assertEqual(instance.search(limit=1, has_attachment=True)[0]["id"], "INBOX|8")

    def test_server_sort_and_rejected_sort_fallback(self):
        instance, connection = backend(self.fixture())
        connection.capabilities = (b"IMAP4REV1", b"SORT=DISPLAY")
        connection.sorted_uids = [3930, 2462]
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|2462")
        self.assertFalse(any("BODYSTRUCTURE" not in args[1] for command, args in connection.commands if command == "FETCH"))
        connection.reject_sort = True
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|2462")

    def test_incomplete_sort_falls_back_to_all_date_headers(self):
        instance, connection = backend(self.fixture())
        connection.capabilities = ("SORT",)
        connection.sorted_uids = [3930]
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|2462")

    def test_missing_or_invalid_header_uses_internaldate(self):
        messages = {1: {"date": "not a date", "internal": "14-Nov-2026 12:00:00 +0800"},
                    2: {"date": "Mon, 26 Aug 2024 09:04:44 -0700"}}
        instance, _ = backend(messages)
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|1")
        self.assertEqual(date_sort_key(None), float("-inf"))

    def test_date_fetch_errors_fail_without_returning_old_subset(self):
        instance, connection = backend(self.fixture())
        connection.reject_fetch = True
        with self.assertRaises(ImapError):
            instance.search(limit=1)

    def test_empty_and_zero_limit(self):
        instance, connection = backend({})
        self.assertEqual(instance.search(limit=1), [])
        self.assertEqual(instance.search(limit=0), [])
        self.assertEqual(connection.commands, [])

    def test_metadata_fetch_is_batched_and_equal_dates_are_stable(self):
        instance, connection = backend({uid: {"date": "14 Nov 2026 12:00:00 +0000"} for uid in range(1, 1002)})
        self.assertEqual(instance.search(limit=1)[0]["id"], "INBOX|1001")
        batches = [args[0] for command, args in connection.commands if command == "FETCH" and "BODYSTRUCTURE" not in args[1]]
        self.assertEqual([len(batch.split(",")) for batch in batches], [100] * 10 + [1])

    def test_slow_index_saves_progress_and_resumes_without_a_partial_result(self):
        messages = {uid: {"date": "14 Nov 2026 12:00:00 +0000"} for uid in range(1, 302)}
        instance, connection = backend(messages)
        clock = [0]
        original = connection.uid
        def slow_fetch(command, *args):
            result = original(command, *args)
            if command == "FETCH":
                clock[0] += 11
            return result
        connection.uid = slow_fetch
        with patch('backend_imap.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(IndexPendingError) as caught:
                instance.search(limit=1)
        self.assertEqual((caught.exception.completed, caught.exception.total), (200, 301))
        instance._reset()
        connection.commands.clear()
        self.assertEqual(instance.search(limit=1)[0]['id'], 'INBOX|301')
        batches = [args[0] for command, args in connection.commands if command == 'FETCH' and 'BODYSTRUCTURE' not in args[1]]
        self.assertEqual([len(batch.split(',')) for batch in batches], [100, 1])

    def test_pending_account_never_returns_a_misleading_partial_latest_set(self):
        accounts = [SimpleNamespace(name='ready'), SimpleNamespace(name='indexing')]
        def search(name):
            if name == 'indexing':
                raise IndexPendingError(100, 1000)
            return [{'date': '2024-08-01T00:00:00+00:00', 'id': 'old'}]
        with patch.object(mailserver, 'all_accounts', return_value=accounts), patch.object(mailserver, 'backend_for', side_effect=lambda name: SimpleNamespace(search=lambda **kw: search(name))):
            with self.assertRaises(mailserver.ToolError) as caught:
                mailserver.mail_search()
        self.assertIsInstance(caught.exception.__cause__, IndexPendingError)

    def test_cross_account_merge_compares_timezones(self):
        accounts = [SimpleNamespace(name="first"), SimpleNamespace(name="second")]
        messages = {"first": [{"date": "2026-11-13T00:30:00+14:00", "id": "first"}],
                    "second": [{"date": "2026-11-12T22:30:00-08:00", "id": "second"}]}
        with patch.object(mailserver, "all_accounts", return_value=accounts), patch.object(mailserver, "backend_for", side_effect=lambda name: SimpleNamespace(search=lambda **kw: messages[name])):
            self.assertEqual([m["id"] for m in mailserver.mail_search()["messages"]], ["second", "first"])

    def test_persistent_cache_survives_restart_and_rejects_new_uidvalidity(self):
        instance, connection = backend(self.fixture())
        instance.account.imap_host = 'imap.example.test'
        instance.account.imap_port = 993
        instance.account.imap_user = 'fixture@example.test'
        instance.search(limit=1)
        instance._reset()
        connection.commands.clear()
        self.assertEqual(instance.search(limit=1)[0]['id'], 'INBOX|2462')
        self.assertFalse(any('BODYSTRUCTURE' not in args[1] for command,args in connection.commands if command == 'FETCH'))
        instance._reset()
        connection.untagged_responses['UIDVALIDITY'] = [b'2']
        connection.messages[2462], connection.messages[3930] = connection.messages[3930], connection.messages[2462]
        self.assertEqual(instance.search(limit=1)[0]['id'], 'INBOX|3930')


if __name__ == "__main__":
    unittest.main(verbosity=2)
