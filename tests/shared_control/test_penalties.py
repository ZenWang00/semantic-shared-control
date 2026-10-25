import math

import pytest

from ssc_core.penalties import HostNormalization, residual_diagnostic


@pytest.mark.parametrize("residual, expected", [(.008, .64), (-.002, 0), (0, 0)])
def test_wiki_distance_examples(residual, expected):
    result = residual_diagnostic(residual, output_unit="m",
                                 normalization=HostNormalization("m", .01, 2.))
    assert result.raw_residual == residual
    assert result.normalization_scale == .01
    assert result.penalty == pytest.approx(expected, abs=1e-9)
    assert result.weighted_penalty == pytest.approx(2 * expected, abs=1e-9)
    assert result.relation_satisfied is (residual <= 1e-9)


@pytest.mark.parametrize("scale", [0, -1, True, math.nan, math.inf, 10**400])
def test_invalid_scale(scale):
    with pytest.raises(ValueError):
        HostNormalization("m", scale)


@pytest.mark.parametrize("weight", [-1, True, math.nan, math.inf, 10**400])
def test_invalid_weight(weight):
    with pytest.raises(ValueError):
        HostNormalization("m", .01, weight)


@pytest.mark.parametrize("g", [True, math.nan, math.inf, 10**400])
def test_invalid_raw_residual(g):
    with pytest.raises(ValueError):
        residual_diagnostic(g, output_unit="m", normalization=HostNormalization("m", .01))


def test_units_overflow_and_hinge_boundaries():
    with pytest.raises(ValueError, match="unit"):
        residual_diagnostic(.008, output_unit="rad", normalization=HostNormalization("m", .01))
    with pytest.raises(ValueError, match="unit"):
        HostNormalization("cm", .01)
    for settings in (HostNormalization("m", 1e-300), HostNormalization("m", 1, 1e300)):
        with pytest.raises(ValueError, match="nonfinite"):
            residual_diagnostic(1e300, output_unit="m", normalization=settings)
    assert residual_diagnostic(-1e300, output_unit="m",
                               normalization=HostNormalization("m", 1e-300)).penalty == 0
    assert residual_diagnostic(.008, output_unit="m",
                               normalization=HostNormalization("m", .01, 0)).penalty > 0
