"""Archival and host features (rejection.py): placeholders, noise and stray points don't become values."""

from dozynki import rejection


def ps1(band, *mags):
    return [{"survey": "ps1", "band": band, "mag": m, "mag_err": 0.05} for m in mags]


def lsst(mjd, band, sci_mag):
    return {"mjd": mjd, "band": band, "sci_mag": sci_mag}


def test_host_placeholder_photoz_is_missing():
    z, _, _ = rejection._host_props({"z_phot_median": -99, "z_phot_l68": -99, "z_phot_u68": -99, "mag_r": 21, "shape_r": 1})
    assert z is None


def test_host_unconstrained_photoz_is_missing():
    z, _, _ = rejection._host_props({"z_phot_median": 0.8, "z_phot_l68": 0.2, "z_phot_u68": 2.0, "mag_r": 21, "shape_r": 1})
    assert z is None


def test_host_good_values_pass():
    assert rejection._host_props({"z_phot_median": 0.3, "z_phot_l68": 0.25, "z_phot_u68": 0.35, "mag_r": 20.5,
                                  "shape_r": 1.2}) == (0.3, 20.5, 1.2)


def test_host_noise_magnitude_is_missing():
    _, mag, _ = rejection._host_props({"z_phot_median": 0.3, "z_phot_l68": 0.25, "z_phot_u68": 0.35, "mag_r": 28.7, "shape_r": 1})
    assert mag is None


def test_no_host_is_all_missing():
    assert rejection._host_props(None) == (None, None, None)


def test_ps1_needs_three_points_in_the_band():
    assert rejection._ps1_mags(ps1("r", 20.0, 20.1), "r") == []
    assert rejection._ps1_mags(ps1("r", 20.0, 20.1, 20.2), "r") == [20.0, 20.1, 20.2]


def test_ps1_brightening_never_mixes_bands():
    # Pan-STARRS1 only has g; LSST only r: no comparison, rather than r against g
    assert rejection._ps1_brightening([lsst(100.1, "r", 18.0)], ps1("g", 20, 20, 20)) is None


def test_ps1_brightening_uses_the_latest_night_median():
    points = [lsst(100.1, "r", 19.0), lsst(100.2, "r", 19.2), lsst(100.3, "r", 25.0),  # one bad point that night
              lsst(50.1, "r", 21.0)]  # an older night
    # median of the latest night (19.2) against Pan-STARRS1's median (21.0)
    assert abs(rejection._ps1_brightening(points, ps1("r", 21.0, 21.0, 21.2)) - 1.8) < 1e-9


def test_excess_rms_ignores_a_few_bad_epochs():
    steady = [{"band": "r", "mag": 18.0 + 0.01 * (i % 3), "mag_err": 0.02} for i in range(30)]
    spiked = steady + [{"band": "r", "mag": 14.0, "mag_err": 0.02}, {"band": "r", "mag": 22.0, "mag_err": 0.02}]
    assert rejection._excess_rms(spiked) < 0.02


def test_excess_rms_sees_real_variability():
    varying = [{"band": "g", "mag": 18.0 + 0.3 * (-1) ** i, "mag_err": 0.02} for i in range(30)]
    assert rejection._excess_rms(varying) > 0.25


def test_excess_rms_needs_enough_points():
    assert rejection._excess_rms([{"band": "r", "mag": 18.0 + i, "mag_err": 0.02} for i in range(5)]) is None
