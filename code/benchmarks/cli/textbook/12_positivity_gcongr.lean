import Mathlib

theorem sq_add_one_pos (x : ℝ) : 0 < x ^ 2 + 1 := by positivity

theorem exp_div_pos (x y : ℝ) (hy : 0 < y) : 0 < Real.exp x / y + x ^ 4 := by positivity

theorem sqrt_nonneg' (x : ℝ) : 0 ≤ Real.sqrt (x ^ 2 + 3) * 2 := by positivity

theorem mul_le_mul_right' (a b c : ℝ) (h : a ≤ b) (hc : 0 ≤ c) : a * c ≤ b * c := by gcongr

theorem exp_mono (x y : ℝ) (h : x ≤ y) : Real.exp x ≤ Real.exp y := by gcongr

theorem pow_mono' (x y : ℝ) (hx : 0 ≤ x) (h : x ≤ y) : x ^ 3 + 1 ≤ y ^ 3 + 1 := by gcongr

theorem div_mono (a b c : ℝ) (hab : a ≤ b) (hc : 0 < c) : a / c ≤ b / c := by gcongr
