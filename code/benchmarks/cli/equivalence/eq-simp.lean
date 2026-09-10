import Mathlib
set_option maxHeartbeats 1000000
-- 40 `simp?` calls: the "Try this: simp only [...]" output names the lemmas the simp set
-- selected *and their order*, so any change in `DiscrTree` insertion order shows up here.
open Finset in
example (n : ℕ) : n + 0 = n := by simp?
example (n : ℕ) : 0 + n = n := by simp?
example (n : ℕ) : n * 1 = n := by simp?
example (n : ℕ) : 1 * n = n := by simp?
example (a b : ℕ) : a + b - b = a := by simp?
example (l : List ℕ) : (l ++ []).length = l.length := by simp?
example (l : List ℕ) : ([] ++ l) = l := by simp?
example (l : List ℕ) : (l.map id) = l := by simp?
example (s : Set ℕ) : s ∪ ∅ = s := by simp?
example (s : Set ℕ) : s ∩ Set.univ = s := by simp?
example (s : Set ℕ) : s \ ∅ = s := by simp?
example (s : Finset ℕ) : s ∪ ∅ = s := by simp?
example (s : Finset ℕ) : (∅ : Finset ℕ) ⊆ s := by simp?
example (x : ℝ) : x + 0 = x := by simp?
example (x : ℝ) : x * 0 = 0 := by simp?
example (x : ℝ) : |(-x)| = |x| := by simp?
example (x : ℝ) (hx : 0 < x) : Real.exp (Real.log x) = x := by simp? [hx]
example (x : ℝ) : Real.cos (-x) = Real.cos x := by simp?
example (x : ℝ) : Real.sin (-x) = -Real.sin x := by simp?
example (z : ℂ) : (starRingEnd ℂ) ((starRingEnd ℂ) z) = z := by simp?
example (G : Type) [Group G] (a : G) : a * a⁻¹ = 1 := by simp?
example (G : Type) [Group G] (a : G) : a⁻¹⁻¹ = a := by simp?
example (G : Type) [Group G] (a b : G) : (a * b)⁻¹ = b⁻¹ * a⁻¹ := by simp?
example (R : Type) [Ring R] (a : R) : a - a = 0 := by simp?
example (R : Type) [Ring R] (a : R) : a * 0 = 0 := by simp?
example (R : Type) [CommRing R] (a b : R) : (a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2 := by ring
example (n : ℕ) : (n : ℤ) ≥ 0 := by simp?
example (f : ℕ → ℕ) (s : Finset ℕ) : ∑ i ∈ (∅ : Finset ℕ), f i = 0 := by simp?
example (f : ℕ → ℕ) (a : ℕ) : ∑ i ∈ ({a} : Finset ℕ), f i = f a := by simp?
example (p q : Prop) [Decidable p] : (if p then q else q) = q := by simp?
example (p : Prop) : ¬¬p ↔ p := by simp?
example (p q : Prop) : (p ∧ True) ↔ p := by simp?
example (α : Type) (a : α) (s : Set α) : a ∈ ({a} : Set α) := by simp?
example (m n : ℕ) : m ∣ m * n := by simp?
example (x : ℕ) : x.succ ≠ 0 := by simp?
example (v : Fin 3 → ℝ) : (Matrix.of fun i j => v i * v j) 0 0 = v 0 * v 0 := by simp?
example (a : ℕ) : max a a = a := by simp?
example (a b : ℕ) : min a b ≤ a := by simp?
example (X : Type) [TopologicalSpace X] : IsOpen (∅ : Set X) := by simp?
example (X : Type) [TopologicalSpace X] (s : Set X) : interior (interior s) = interior s := by simp?
-- plain `simp` on goals where several lemmas could fire (result depends on the chosen lemma)
example (a b c : ℕ) : a + b + c = c + b + a := by simp [Nat.add_comm, Nat.add_left_comm, Nat.add_assoc]
example (s t : Set ℕ) : (s ∩ t) ∪ (s ∩ tᶜ) = s := by simp [Set.inter_union_compl]
