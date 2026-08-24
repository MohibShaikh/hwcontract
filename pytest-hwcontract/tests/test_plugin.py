import pytest

GOOD_OBS = [{"name": "T0H", "value": 333}, {"name": "T0L", "value": 792},
            {"name": "T1H", "value": 833}, {"name": "T1L", "value": 417},
            {"name": "RESET", "value": 60583}]
BAD_OBS = [{"name": "T0H", "value": 333}, {"name": "T0L", "value": 792},
           {"name": "T1H", "value": 833}, {"name": "T1L", "value": 900},
           {"name": "RESET", "value": 60583}]


def test_bundled_contract_passes_by_name(pytester):
    pytester.makepyfile(
        f"""
        OBS = {GOOD_OBS!r}

        def test_strip(hwcontract):
            hwcontract.timing("ws2812b", OBS)
        """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_failing_edge_is_a_failing_test(pytester):
    pytester.makepyfile(
        f"""
        OBS = {BAD_OBS!r}

        def test_strip(hwcontract):
            hwcontract.timing("ws2812b", OBS)
        """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    assert "hwcontract verdict FAIL" in result.stdout.str()


def test_failure_message_names_the_edge_and_delta(pytester):
    pytester.makepyfile(
        f"""
        import pytest
        OBS = {BAD_OBS!r}

        def test_strip(hwcontract):
            with pytest.raises(AssertionError) as e:
                hwcontract.timing("ws2812b", OBS)
            msg = str(e.value)
            assert "T1L" in msg and "450ns long" in msg, msg
            assert "verdict FAIL" in msg
        """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_marginal_fails_the_test(pytester):
    pytester.makepyfile(
        """
        def test_strip(hwcontract):
            obs = [{"name": "T0H", "value": 333}, {"name": "T0L", "value": 792},
                   {"name": "T1H", "value": 833}, {"name": "T1L", "value": 583},
                   {"name": "RESET", "value": 60583}]
            hwcontract.timing("ws2812b", obs)
        """)
    result = pytester.runpytest()
    result.assert_outcomes(failed=1)
    assert "MARGINAL" in result.stdout.str()


def test_serial_judgment(pytester):
    pytester.makepyfile(
        """
        def test_boot(hwcontract):
            hwcontract.serial("boot", "boot v3\\nIMU init OK\\nready\\n")

        def test_bad_boot(hwcontract):
            hwcontract.serial("boot", "Guru Meditation Error\\n")
        """)
    result = pytester.runpytest()
    result.assert_outcomes(passed=1, failed=1)
    assert "forbidden match: Guru Meditation" in result.stdout.str()


def test_contract_not_found_names_the_search(pytester):
    pytester.makepyfile(
        """
        import pytest

        def test_x(hwcontract):
            with pytest.raises(Exception, match="no contract file"):
                hwcontract.timing("nope", [])
        """)
    pytester.runpytest().assert_outcomes(passed=1)


def test_root_option_resolves_contracts(pytester):
    pytester.makefile(".contract.yaml", mine=(
        "contract: mine\nheadroom_pct: 20\nedges:\n"
        "  - {name: OK, min: 0, typ: 10, max: 20}\n"))
    pytester.makepyfile(
        """
        def test_x(hwcontract):
            hwcontract.timing("mine.contract.yaml", [{"name": "OK", "value": 10}])
        """)
    pytester.runpytest("--hwcontract-root", str(pytester.path)).assert_outcomes(passed=1)


def test_capture_csv_helper(pytester):
    pytester.makefile(".csv", cap="0\n1\n" * 4 + "0\n")
    pytester.makepyfile(
        """
        def test_x(hwcontract):
            obs = hwcontract.capture_csv("cap.csv", samplerate=24_000_000)
            assert isinstance(obs, list)
        """)
    pytester.runpytest().assert_outcomes(passed=1)
