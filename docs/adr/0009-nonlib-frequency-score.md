# The nonlib frequency score is calculated correctly

---
status: accepted — verification for #7; the hover text is fixed here, the rest is upstream in mcrit
---

Issue #7 asks for the calculation of the nonlib frequency score to be verified,
because "in some text examples this value seemed to be too far from the expected
value". The arithmetic is not in this repository — it is
`MatcherInterface._aggregateMatchSampleSummary` and `_get_family_adjustment` in
mcrit — but the captured reports in `tests/fixtures/` carry both its inputs
(`matches.functions`, every function of the reference sample with its byte weight,
instruction count and match list) and its outputs
(`matches.samples[*].matched.percent`). That makes the claim checkable from here.

**The arithmetic is right.** An independent transcription of the formula, run over
all three captured reports, reproduces all 150 shipped percentage cells exactly (to
1e-9). No numerator is lost, no denominator is wrong for what the value is defined
to be, and nothing double-counts. `tests/testScoreArithmetic.py` is that
transcription; five separate mutations of it — wrong denominator, no family
adjustment, `>` for `>=` at the size threshold, the nonlib columns sharing the plain
denominator, and dropping the match score from the weighting — each make it fail.

What is wrong is everything around the number. Four things move the displayed score
away from what a reader computes, and the largest of them is in this repository.

## What the value is defined to be

For each foreign sample, mcrit walks the reference sample's functions that matched
it, and accumulates three byte totals plus three `nonlib_` twins that skip any
function which matched a library sample anywhere in the corpus:

    unweighted          += own_function.binweight
    score_weighted      += own_function.binweight * best_match_score / 100
    frequency_weighted  += score_weighted_increment / family_adjustment[fid]

`family_adjustment` is the count of distinct families that function turns up in,
log-binned: `1 if n < 3 else 1 + int(log2(n))`. The six totals then become
percentages against **two** divisors:

| | divisor | `matches_for_sample` |
| --- | --- | --- |
| plain columns | matchable bytes (`num_instructions >= MINHASH_FN_MIN_INS`) | 153466.0 |
| `nonlib_` columns | matchable bytes − bytes of library-matching functions | 152337.0 |
| `uniq_score` (`MatchingResult`) | the sample's full binweight | 155065.0 |

So `nl_freq > freq` is arithmetically correct for any sample carrying library bytes.
On the top match of `matches_for_sample` it is 85.7854 against 85.6635.

## Why it looks too far off, in descending order of size

**1. The hover text divided by the wrong number — and that one is ours, so it is
fixed here.** All twenty score tooltips across
`result_compare_{all,family,sample,vs}.html` rendered

    Bytes: {matched_bytes_*} / {reference_sample_entry.binweight}
    Percent: {matched_percent_*}%

The percent on the second line is not the quotient on the first. For sample 1 of
`matches_for_sample` the tooltip offers 130682.88 / 155065 = **84.28** and then
states **85.79**, a gap of 1.51 points; the plain frequency column's gap is 0.88.
A reader who checked the arithmetic in front of them found it did not hold, on the
`nonlib_` column worst — exactly the complaint in #7. Issue #7 reports a value being
too far from the *expected* value; since the arithmetic is right, the expectation is
what was wrong, and this is what set it.

The gap was not bounded. It scales as `binweight / (matchable − library)`, so it grows
with the library share of the sample: 1.0% relative at this corpus's 0.7% library
bytes, 12% at a 10% library share, 44% at 30%, 102% at half.

`recover_score_divisors()` in `mcritweb/views/data.py` now recovers both totals as
`100 * matched_bytes_X / matched_percent_X` and the templates print those. It reads
the *unfiltered* matches, so a `?famid=`/`?samid=` page shows the same totals as the
unfiltered one, and it needs no threshold logic of its own — see "Consequences".

**2. Four columns, two divisors, one header.** The `direct`/`frequency` pairs sit
side by side with nothing saying they are normalised against different totals, and
`uniq_score` in the same table uses a third. A reader comparing across columns reads
a correct result as an inconsistency.

**3. Truncation — real, but bounded by one point.** `%d` truncates toward zero, so
85.7854 rendered as `85`. Across the 150 cells of the three fixtures the truncated
value is off by 0.241 on average and by at most 0.994, and 29 cells were off by half
a point or more. `%3.0f` brings that to 0.153 average, 0.470 worst, no cell off by
half a point. This is what the rest of PR #148 fixes, and it is worth being precise
that **it cannot be the whole answer**: it is off by less than one point by
construction, while (1) is unbounded.

**4. The frequency score is corpus-dependent, and degenerate on a VS report.** The
family adjustment is a step function — 2 families divide by 1, 3 families divide by
2 — so indexing one sample of a third family halves a function's contribution.
More sharply: on a 1-vs-1 comparison `_summarizeMatches` drops self-matches, so every
matched function sees exactly one foreign family and the divisor is always 1.
`matches_for_sample_vs` demonstrates it: all 422 matched functions have one family
and `frequency_weighted == score_weighted == 42.8128` exactly. Anyone cross-checking
a compare-all frequency score against a VS run of the same pair is comparing two
different quantities.

## Two defects found in mcrit. Neither is reachable today

Reported here rather than fixed; mcrit is a separate repository.

**`MatchedFunctionEntry` stores flag bits under boolean names, and its round trip
corrupts them.** `match_is_pichash = match_tuple[4] & IS_PICHASH_FLAG` is `2`, not
`True`, and `match_is_library` is `4`. `getMatchTuple()` then multiplies each by its
flag again, so `toDict()`/`fromDict()` maps flags 1→1, 2→4, 3→5, 4→16, 6→20, 7→21:
a pichash-only match round-trips into a *library* match, which would corrupt every
`nonlib_` column. It is latent only because `MatchingResult.toDict()` has no
production caller in either repository — mcritweb calls `fromDict` exclusively.

The same field explains a standing puzzle in our tree. `MatchReportRenderer.py:249`
reads `# TODO for some reason, we get match scores of 102 here? maybe related to how
match_is_pichash is used`, and line 269 computes `match.matched_score +
match.match_is_pichash`. A pichash match scores 100 and contributes `2`. The guess in
the TODO was right. That method is dead on the web path (PR #148 verified it: reached
only from `main()`, and `setup.py` declares no console script), so this is diagnosis,
not an outage.

**Two size thresholds that are only equal by default.** The denominator counts
functions at `num_instructions >= MINHASH_FN_MIN_INS` (10), minhashing requires
`> MINHASH_FN_MIN_INS`, and pichash matching requires `>= PICHASH_SIZE` (10). The
first two disagree by one: a function of exactly 10 instructions is in the
denominator but can only ever reach the numerator by pichash. That is 18 functions
and 0.34% of matchable bytes here — real, negligible. The second pair is the
dangerous one: `PICHASH_SIZE` and `MINHASH_FN_MIN_INS` are independent config keys,
and setting the former below the latter puts bytes in the numerator that are not in
the denominator, letting percentages exceed 100.

One more, unquantified: `MatchingResult.getUniqueFamilyScoreForSample` *assigns*
rather than maxes `weighted_bytes_per_function_id`, so a function with several
matches keeps whichever score iterated last rather than its best.

## Consequences

`Closes #7` is honest: the calculation was checked and is correct, the reasons it
read wrong are named and sized, and the one of them that was ours — the hover text
that set the reader's expectation — is fixed in the same change.

Neither divisor is on the wire — `SampleEntry` carries `binweight` and SMDA
statistics and nothing carries the matchable or nonlibrary totals, and the function
list mcritweb receives holds no instruction counts and omits unmatched functions
entirely, so neither can be *recomputed* here. mcrit should still export
`own_sample_num_matchable_bytes` and `own_sample_num_nonlibrary_bytes` on the report,
and this ADR is the argument for it.

What made the fix possible without them is that the divisor need not be recomputed,
only recovered: it is a property of the reference sample, identical on every row, and
`100 * matched_bytes_X / matched_percent_X` inverts it exactly from any row that
scored above zero. Verified against both totals on all three fixtures, where every
qualifying row recovers the same value to the last bit. So the page duplicates none
of mcrit's threshold logic and does not go stale if `MINHASH_FN_MIN_INS` changes.

**The one case it cannot invert is a group where every row scored zero**, since the
numerator is then zero and the quotient is 0/0. There is no such column in the three
fixtures. The fallbacks are the matchable total, then the binweight; a zero numerator
reads as 0% over any total, so the fraction stays consistent with its percentage
rather than going inconsistent with the cells beside it. No cell is left unrecovered
in a way a reader could see.

The two decimals the tooltip prints are its only remaining imprecision: the stated
percentage can differ from the stated fraction by up to 0.005, which is what the
tolerance in both tests allows and nothing wider.

Three tests hold this down. `tests/testScoreArithmetic.py` pins the verification
itself; it guards a finding rather than a behaviour, so it passes both before and
after, and what it catches is a re-captured fixture
(`tests/fixtures/regenerate.py`) that no longer agrees with the formula documented
here — precisely when this document needs revisiting.
`testResultPages.test_score_tooltips_divide_by_the_total_their_percentage_uses`
guards the fix, and fails on any one of the twenty cells reverted individually.
`tests/testBrowser.py` walks the four templates in Chromium, because the offline
tests read HTML as text and cannot see that `&#10;` becomes a newline in the
attribute or that `hint.css` draws the text at all; it skips where playwright is
absent, which includes CI.
