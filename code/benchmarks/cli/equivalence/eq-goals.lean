import Mathlib

-- Prebuilt-search-index equivalence suite: 20+ goals across Mathlib areas for `exact?` /
-- `apply?` / `rw?`.
-- Every suggestion list (text and order) must be identical between stock and fork.
set_option maxHeartbeats 400000

-- exact?
example (a b : ℕ) : a + b = b + a := by exact?
example (a b : ℕ) : a ≤ a + b := by exact?
example (a : ℝ) (h : 0 < a) : 0 < a ^ 2 := by exact?
example (s t : Set ℕ) : s ∪ t = t ∪ s := by exact?
example (x y : ℝ) (hx : 0 ≤ x) (hy : 0 ≤ y) : 0 ≤ x * y := by exact?
example (l : List ℕ) : l.reverse.reverse = l := by exact?
example (n : ℕ) : 0 < n.factorial := by exact?
example (p : ℕ) (hp : p.Prime) : 2 ≤ p := by exact?
example (G : Type*) [Group G] (a b : G) : (a * b)⁻¹ = b⁻¹ * a⁻¹ := by exact?
example (X : Type*) [TopologicalSpace X] (s : Set X) : IsOpen (interior s) := by exact?
example (a b : ℤ) : a ∣ a * b := by exact?
example (x : ℝ) : 0 < Real.exp x := by exact?
example (m n : ℕ) (h : m ∣ n) (hn : 0 < n) : m ≤ n := by exact?
example (f : ℕ → ℕ) (hf : StrictMono f) : Function.Injective f := by exact?
example (s : Finset ℕ) (a : ℕ) (h : a ∈ s) : {a} ⊆ s := by exact?
example (z : ℂ) : ‖z‖ ^ 2 = Complex.normSq z := by exact?

-- apply?
example (a b c : ℝ) (h : a ≤ b) : a + c ≤ b + c := by apply?
example (s t : Set ℕ) (h : s ⊆ t) : s ∩ t = s := by apply?
example (n : ℕ) (h : 0 < n) : n - 1 + 1 = n := by apply?
example (a b : ℝ) (ha : 0 < a) (hb : 0 < b) : 0 < a / b := by apply?

-- apply? with partial suggestion lists (goal not closed)
example (a b c : ℝ) (h : a < b) : a * c < b * c := by apply?
example (x y : ℝ) : x ≤ max x y + 1 := by apply?

-- rw?
example (a b : ℕ) : a + b = b + a := by rw?
example (l : List ℕ) : (l ++ []).length = l.length := by rw?
example (x : ℝ) : Real.exp (x + x) = Real.exp x * Real.exp x := by rw?
example (a b : ℚ) : a * b = b * a := by rw?

-- rw? with rewrite lists (goal not closed by one rewrite)
example (a b c : ℕ) : a * (b + c) = c * a + a * b := by rw?
example (x : ℝ) : Real.exp (2 * x) = Real.exp x ^ 2 := by rw?
