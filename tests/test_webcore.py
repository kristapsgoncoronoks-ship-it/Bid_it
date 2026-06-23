import app as A
import webcore


def test_app_uses_webcore_rendering_helpers():
    assert A.page is webcore.page
    assert A._review_page is webcore._review_page
    assert A.tbl is webcore.tbl
    assert A._csrf_input is webcore._csrf_input
    assert A._log_exc is webcore._log_exc


def test_webcore_page_shell_is_configured():
    with A.app.test_request_context("/"):
        html = webcore.page("<div>payload</div>", "home")
    assert "<main><div>payload</div></main>" in html
    assert 'name="_csrf"' in html


def test_webcore_move_keeps_endpoint_coverage_closed():
    assert A._assert_endpoint_coverage() == set()
