import Mathlib
-- 25 `#synth` queries: the instance chosen (and hence printed) depends on the order of the
-- instance `DiscrTree` and on instance priorities.
#synth AddCommMonoid ℕ
#synth CommRing ℤ
#synth Field ℚ
#synth LinearOrder ℝ
#synth ConditionallyCompleteLinearOrder ℝ
#synth Field ℂ
#synth NormedField ℂ
#synth TopologicalSpace ℝ
#synth MetricSpace ℝ
#synth CompleteSpace ℝ
#synth Group (Equiv.Perm (Fin 5))
#synth Fintype (Fin 7)
#synth DecidableEq ℕ
#synth Module ℝ (Fin 3 → ℝ)
#synth AddCommGroup (Matrix (Fin 2) (Fin 2) ℝ)
#synth Ring (Matrix (Fin 2) (Fin 2) ℝ)
#synth Monoid (ℕ → ℕ)
#synth Lattice (Set ℕ)
#synth CompleteLattice (Set ℕ)
#synth Preorder (ℕ × ℕ)
#synth SemilatticeSup ℕ
#synth Algebra ℝ ℂ
#synth NormedAddCommGroup (EuclideanSpace ℝ (Fin 3))
#synth MeasurableSpace ℝ
#synth Countable ℚ
#synth Nonempty ℝ
#synth Inhabited (List ℕ)
#synth Coe ℕ ℤ
-- instance printing (delaborator/unexpander tables are deferred too)
#check (inferInstance : AddCommGroup ℤ)
#print Nat.instAddCommMonoid
