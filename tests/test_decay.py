from snagline.decay import ScoreDecay

def test_score_decay_half_life():
    decay = ScoreDecay(half_life_seconds=100.0)
    score = decay.decay_score(1.0, 100.0)
    assert abs(score - 0.5) < 1e-4

def test_score_decay_zero_elapsed():
    decay = ScoreDecay(half_life_seconds=100.0)
    assert decay.decay_score(0.8, 0.0) == 0.8
