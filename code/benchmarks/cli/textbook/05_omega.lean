import Mathlib

theorem odd_add_odd (n m : ℕ) (hn : n % 2 = 1) (hm : m % 2 = 1) : (n + m) % 2 = 0 := by omega

theorem int_bound (x y : ℤ) (h : 3 * x + 2 * y = 7) (hx : 0 ≤ x) (hy : 0 ≤ y) : x ≤ 2 := by omega

theorem div_mod_facts (n : ℕ) (h : n % 4 = 3) : n % 2 = 1 ∧ (n + 1) % 4 = 0 := by omega

theorem sub_lemma (a b c : ℕ) (h : a ≤ b) (h' : b < c) : c - a ≥ 1 := by omega
