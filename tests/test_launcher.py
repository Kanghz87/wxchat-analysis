"""启动入口回归：模拟服务和浏览器，不打开真实网页或读取微信数据。"""
from contextlib import ExitStack
from io import StringIO
import unittest
from unittest.mock import Mock, patch

import web_app


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = self.enterContext(ExitStack())
        self.output = stack.enter_context(patch("sys.stdout", new_callable=StringIO))
        self.error = stack.enter_context(patch("sys.stderr", new_callable=StringIO))
        self.probe = stack.enter_context(patch("web_app.socket.socket")).return_value.__enter__.return_value
        self.app = stack.enter_context(patch("web_app.create_app"))
        self.factory = stack.enter_context(patch("web_app.create_server"))
        self.server = self.factory.return_value
        self.browser = stack.enter_context(patch("web_app.webbrowser.open", return_value=True))

    def run_main(self, *args: str) -> None:
        with patch("sys.argv", ["web_app.py", *args]):
            web_app.main()

    def test_manual_launch_remains_local_without_browser(self) -> None:
        self.run_main()
        self.probe.bind.assert_called_once_with(("127.0.0.1", 8501))
        self.factory.assert_called_once_with(self.app.return_value, host="127.0.0.1", port=8501, threads=4)
        self.browser.assert_not_called()
        self.server.run.assert_called_once()
        self.server.close.assert_called_once()

    def test_browser_opens_after_binding_before_service_loop(self) -> None:
        order = []
        self.factory.side_effect = lambda *args, **kwargs: order.append("ready") or self.server
        self.browser.side_effect = lambda *args, **kwargs: order.append("browser") or True
        self.server.run.side_effect = lambda: order.append("run")
        self.run_main("--open-browser", "--port", "8502")
        self.assertEqual(order, ["ready", "browser", "run"])
        self.browser.assert_called_once_with("http://127.0.0.1:8502", new=2)
        self.server.close.assert_called_once()

    def test_repeated_launch_reuses_existing_analyzer(self) -> None:
        self.probe.bind.side_effect = OSError("busy")
        with patch("web_app.is_running_analyzer", return_value=True) as existing:
            self.run_main("--open-browser")
        existing.assert_called_once_with(8501)
        self.browser.assert_called_once_with("http://127.0.0.1:8501", new=2)
        self.app.assert_not_called()
        self.factory.assert_not_called()

    def test_other_occupied_port_reports_error_without_opening(self) -> None:
        self.probe.bind.side_effect = OSError("busy")
        with patch("web_app.is_running_analyzer", return_value=False):
            with self.assertRaises(SystemExit) as error:
                self.run_main("--open-browser")
        self.assertEqual(error.exception.code, 1)
        self.assertIn("8501", self.error.getvalue())
        self.browser.assert_not_called()
        self.app.assert_not_called()

    def test_browser_failure_keeps_service_running(self) -> None:
        for failure in (False, OSError("no browser")):
            with self.subTest(failure=failure):
                self.browser.reset_mock()
                self.server.reset_mock()
                self.browser.side_effect = failure if isinstance(failure, Exception) else None
                self.browser.return_value = False
                self.run_main("--open-browser")
                self.assertIn("请手动访问：http://127.0.0.1:8501", self.output.getvalue())
                self.server.run.assert_called_once()
                self.server.close.assert_called_once()

    def test_running_instance_marker_is_checked_locally(self) -> None:
        response = Mock(status=200)
        with patch("web_app.HTTPConnection") as connect:
            connection = connect.return_value
            connection.getresponse.return_value = response
            for body, expected in ((b'{"app":"wxchat-analysis"}', True),
                                   (b'{"app":"something-else"}', False), (b"invalid", False), (b"[]", False)):
                with self.subTest(body=body):
                    response.read.return_value = body
                    self.assertEqual(web_app.is_running_analyzer(8502), expected)
            connect.assert_called_with("127.0.0.1", 8502, timeout=1)
            connection.request.assert_called_with("GET", "/api/health")
            self.assertEqual(connection.close.call_count, 4)


if __name__ == "__main__":
    unittest.main()
