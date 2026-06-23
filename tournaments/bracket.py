import math


def _bracket_positions(bracket_size):
    """
    Returns seed positions in bracket slot order so that the top seed can
    only meet the 2nd seed in the final, top-half seeds stay in the top half, etc.
    e.g. bracket_size=8 → [1, 8, 4, 5, 3, 6, 2, 7]
    """
    if bracket_size == 2:
        return [1, 2]
    prev = _bracket_positions(bracket_size // 2)
    result = []
    for p in prev:
        result.append(p)
        result.append(bracket_size + 1 - p)
    return result


# ── Single Elimination ────────────────────────────────────────────────────────

def generate_single_elimination(tournament):
    from .models import Match

    tournament.matches.all().delete()

    entries = list(tournament.entries.order_by('seed'))
    n = len(entries)
    if n < 2:
        return

    num_rounds = math.ceil(math.log2(n))
    bracket_size = 2 ** num_rounds

    seed_map = {e.seed: e for e in entries}
    positions = _bracket_positions(bracket_size)
    # positions[i] is the seed that goes in slot i; None if seed > n (bye)
    slots = [seed_map.get(pos) for pos in positions]

    # ── Round 1 ──────────────────────────────────────────────────────────────
    round1_matches = []
    for i in range(0, bracket_size, 2):
        p1 = slots[i]
        p2 = slots[i + 1]
        is_bye = (p1 is None) or (p2 is None)
        winner = None
        if is_bye:
            winner = p1 or p2

        m = Match.objects.create(
            tournament=tournament,
            round_number=1,
            match_number=(i // 2) + 1,
            player1=p1,
            player2=p2,
            winner=winner,
            is_bye=is_bye,
        )
        round1_matches.append(m)

    # ── Later rounds (empty placeholders) ────────────────────────────────────
    prev_matches = round1_matches
    for rnd in range(2, num_rounds + 1):
        new_matches = []
        for i in range(0, len(prev_matches), 2):
            m = Match.objects.create(
                tournament=tournament,
                round_number=rnd,
                match_number=(i // 2) + 1,
            )
            new_matches.append(m)
        prev_matches = new_matches

    # ── Propagate bye winners into round 2 ───────────────────────────────────
    for m in round1_matches:
        if m.is_bye and m.winner:
            _advance_winner(tournament, m)


def _advance_winner(tournament, match):
    """Push match.winner into the appropriate slot of the next-round match."""
    from .models import Match

    next_round = match.round_number + 1
    next_match_number = math.ceil(match.match_number / 2)

    try:
        next_match = Match.objects.get(
            tournament=tournament,
            round_number=next_round,
            match_number=next_match_number,
        )
    except Match.DoesNotExist:
        return  # was the final

    if match.match_number % 2 == 1:
        next_match.player1 = match.winner
    else:
        next_match.player2 = match.winner

    # If both slots now filled by byes, auto-advance again
    if next_match.player1 and next_match.player2:
        next_match.save()
    elif next_match.player1 or next_match.player2:
        next_match.save()


def record_result(match, winner_entry):
    """Record a match result and advance the winner to the next round."""
    match.winner = winner_entry
    match.save()
    _advance_winner(match.tournament, match)

    # Check if tournament is complete
    tournament = match.tournament
    all_matches = tournament.matches.all()
    if all(m.winner is not None for m in all_matches):
        tournament.status = 'completed'
        tournament.save()


# ── Double Elimination ────────────────────────────────────────────────────────

def generate_double_elimination(tournament):
    from .models import Match

    tournament.matches.all().delete()

    entries = list(tournament.entries.order_by('seed'))
    n = len(entries)
    if n < 2:
        return

    wb_rounds = math.ceil(math.log2(n))
    bracket_size = 2 ** wb_rounds

    seed_map = {e.seed: e for e in entries}
    positions = _bracket_positions(bracket_size)
    slots = [seed_map.get(pos) for pos in positions]

    # ── Winners Bracket Round 1 ───────────────────────────────────────────────
    wb_r1_matches = []
    for i in range(0, bracket_size, 2):
        p1 = slots[i]
        p2 = slots[i + 1]
        is_bye = (p1 is None) or (p2 is None)
        winner = (p1 or p2) if is_bye else None
        m = Match.objects.create(
            tournament=tournament,
            bracket='winners',
            round_number=1,
            match_number=(i // 2) + 1,
            player1=p1,
            player2=p2,
            winner=winner,
            is_bye=is_bye,
        )
        wb_r1_matches.append(m)

    # ── Winners Bracket later rounds ─────────────────────────────────────────
    prev_wb = wb_r1_matches
    for rnd in range(2, wb_rounds + 1):
        new_wb = []
        for i in range(0, len(prev_wb), 2):
            m = Match.objects.create(
                tournament=tournament,
                bracket='winners',
                round_number=rnd,
                match_number=(i // 2) + 1,
            )
            new_wb.append(m)
        prev_wb = new_wb

    # ── Losers Bracket ────────────────────────────────────────────────────────
    # LB has 2*(wb_rounds - 1) rounds for bracket_size >= 4
    lb_rounds = 2 * (wb_rounds - 1)
    if lb_rounds > 0:
        # Each pair of LB rounds shares the same match count:
        # (LBR1, LBR2): B/4 matches, (LBR3, LBR4): B/8, ...
        count = bracket_size // 4
        for r in range(1, lb_rounds + 1):
            for m_num in range(1, count + 1):
                Match.objects.create(
                    tournament=tournament,
                    bracket='losers',
                    round_number=r,
                    match_number=m_num,
                )
            if r % 2 == 0:
                count //= 2

    # ── Grand Final ───────────────────────────────────────────────────────────
    Match.objects.create(
        tournament=tournament,
        bracket='grand_final',
        round_number=1,
        match_number=1,
    )

    # ── Mark LB matches whose both slots are phantoms (cascade of WB byes) ───
    # These matches can never receive a real player and should be skipped
    # entirely in tournament completion checks.
    wb_byes = _get_wb_byes(tournament)
    for lb_match in tournament.matches.filter(bracket='losers').order_by('round_number', 'match_number'):
        if _lb_match_is_double_phantom(tournament, lb_match.round_number, lb_match.match_number, wb_byes):
            lb_match.is_bye = True
            lb_match.save()

    # ── Propagate WBR1 byes ───────────────────────────────────────────────────
    for m in wb_r1_matches:
        if m.is_bye and m.winner:
            _de_advance(tournament, m)


def _get_wb_rounds(tournament):
    n = tournament.entries.count()
    return math.ceil(math.log2(max(n, 2)))


def _de_set_slot(tournament, bracket, round_num, match_num, player, slot):
    """Fill player into slot 1 or 2 of a match."""
    from .models import Match
    try:
        m = Match.objects.get(
            tournament=tournament,
            bracket=bracket,
            round_number=round_num,
            match_number=match_num,
        )
    except Match.DoesNotExist:
        return
    if slot == 1:
        m.player1 = player
    else:
        m.player2 = player
    m.save()


def _get_wb_byes(tournament):
    from .models import Match
    return set(Match.objects.filter(
        tournament=tournament, bracket='winners', is_bye=True
    ).values_list('round_number', 'match_number'))


def _lb_slot_is_phantom(tournament, lb_round, lb_match_num, slot, wb_byes):
    """True if this LB slot can never be filled by a real player due to a chain of WB byes."""
    if lb_round == 1:
        # LBR1 slot 1 ← WBR1 M(2k-1); slot 2 ← WBR1 M(2k).
        wb_match_num = 2 * lb_match_num - 1 if slot == 1 else 2 * lb_match_num
        return (1, wb_match_num) in wb_byes

    if lb_round % 2 == 0:
        # Even LB round: slot 1 ← LBR(r-1) winner; slot 2 ← WBR(r/2+1) loser.
        # WB rounds >= 2 are never byes in our generation.
        if slot == 1:
            return _lb_match_is_double_phantom(tournament, lb_round - 1, lb_match_num, wb_byes)
        return False

    # Odd LB round > 1: both slots ← LBR(r-1) winners (pair consolidation).
    prev_m = 2 * lb_match_num - 1 if slot == 1 else 2 * lb_match_num
    return _lb_match_is_double_phantom(tournament, lb_round - 1, prev_m, wb_byes)


def _lb_match_is_double_phantom(tournament, lb_round, lb_match_num, wb_byes):
    return (_lb_slot_is_phantom(tournament, lb_round, lb_match_num, 1, wb_byes)
            and _lb_slot_is_phantom(tournament, lb_round, lb_match_num, 2, wb_byes))


def _de_set_lb_slot(tournament, lb_round, lb_match_num, player, slot):
    """
    Place a player into a losers-bracket slot. If the opposing slot is a phantom
    (will never be filled because of a WB bye cascade), mark the match as a bye
    with the placed player as winner and recursively advance.
    """
    from .models import Match

    _de_set_slot(tournament, 'losers', lb_round, lb_match_num, player, slot)

    other_slot = 2 if slot == 1 else 1
    wb_byes = _get_wb_byes(tournament)
    if not _lb_slot_is_phantom(tournament, lb_round, lb_match_num, other_slot, wb_byes):
        return

    try:
        lb_match = Match.objects.get(
            tournament=tournament, bracket='losers',
            round_number=lb_round, match_number=lb_match_num,
        )
    except Match.DoesNotExist:
        return

    lb_match.is_bye = True
    lb_match.winner = player
    lb_match.save()
    _de_advance(tournament, lb_match)


def _de_advance(tournament, match):
    """Route winner (and WB loser) after a double-elimination match result."""
    from .models import Match

    wb_rounds = _get_wb_rounds(tournament)
    lb_rounds = 2 * (wb_rounds - 1)

    loser = match.player2 if match.winner == match.player1 else match.player1

    if match.bracket == 'winners':
        # ── Route winner forward in WB or to Grand Final ──────────────────
        if match.round_number < wb_rounds:
            next_m = math.ceil(match.match_number / 2)
            slot = 1 if match.match_number % 2 == 1 else 2
            _de_set_slot(tournament, 'winners', match.round_number + 1, next_m, match.winner, slot)
        else:
            # WB Final winner → Grand Final player 1
            _de_set_slot(tournament, 'grand_final', 1, 1, match.winner, 1)

        # ── Route loser to LB (skip byes — no real loser) ────────────────
        if loser and not match.is_bye and lb_rounds > 0:
            if match.round_number == 1:
                # WBR1 losers pair up in LBR1
                lb_m = math.ceil(match.match_number / 2)
                lb_slot = 1 if match.match_number % 2 == 1 else 2
                _de_set_lb_slot(tournament, 1, lb_m, loser, lb_slot)
            else:
                lb_r = 2 * (match.round_number - 1)
                lb_m = _wb_loser_lb_match(match.round_number, match.match_number, wb_rounds)
                _de_set_lb_slot(tournament, lb_r, lb_m, loser, 2)

    elif match.bracket == 'losers':
        if match.round_number < lb_rounds:
            next_r, next_m, slot = _lb_next_slot(match.round_number, match.match_number)
            _de_set_lb_slot(tournament, next_r, next_m, match.winner, slot)
        else:
            # LB Final winner → Grand Final player 2
            _de_set_slot(tournament, 'grand_final', 1, 1, match.winner, 2)

    elif match.bracket == 'grand_final':
        if match.round_number == 1:
            # Determine which side won:
            # p1 = WB side, p2 = LB side
            # If LB side (p2) wins → bracket reset
            if match.winner == match.player2:
                try:
                    Match.objects.get(
                        tournament=tournament,
                        bracket='grand_final',
                        round_number=2,
                        match_number=1,
                    )
                except Match.DoesNotExist:
                    Match.objects.create(
                        tournament=tournament,
                        bracket='grand_final',
                        round_number=2,
                        match_number=1,
                        player1=match.player1,
                        player2=match.player2,
                    )
        # round 2 (bracket reset) winner = champion; no further routing needed


def _wb_loser_lb_match(wb_round, wb_match_num, wb_rounds):
    """
    LB match number a WBR(k>=2) loser drops into.

    For WBR2, reverse the match order so a loser doesn't immediately rematch
    the WBR1 opponent they just beat. (WBR1 M_k loser ends up in LBR2 M_k slot 1
    via LBR1; routing the WBR2 M_k loser into the *opposite* LBR2 match keeps
    them apart.) Deeper rounds keep the natural mapping — reversing further
    introduces new same-WBR2-pair rematches in LBR4.
    """
    if wb_round == 2:
        lb_match_count = 2 ** (wb_rounds - 2)
        return lb_match_count - wb_match_num + 1
    return wb_match_num


def _lb_next_slot(r, m):
    """
    Return (next_round, next_match_num, slot) for a LB match winner.

    Odd LB rounds: winner goes to next round, same match number, slot 1.
    Even LB rounds: winner goes to next round, match ceil(m/2),
                    slot 1 if m is odd, slot 2 if m is even.
    """
    if r % 2 == 1:
        return r + 1, m, 1
    else:
        return r + 1, math.ceil(m / 2), (1 if m % 2 == 1 else 2)


def record_de_result(match, winner_entry):
    """Record a double-elimination match result and route players."""
    match.winner = winner_entry
    match.save()
    _de_advance(match.tournament, match)

    tournament = match.tournament
    # Skip phantom matches (no players ever assigned) when checking completion.
    if all(m.winner_id is not None for m in tournament.matches.all() if m.player1_id or m.player2_id):
        tournament.status = 'completed'
        tournament.save()


# ── Undo ──────────────────────────────────────────────────────────────────────

def undo_result(match):
    """
    Reverse a recorded match result. Returns (success: bool, error: str | None).
    Refuses if any downstream match has been played by the user.
    """
    if not match.winner_id:
        return False, "Match has no result to undo."
    if match.is_bye:
        return False, "Bye matches are assigned automatically and cannot be undone."

    fmt = match.tournament.format
    if fmt == 'double_elim':
        return _undo_de(match)
    if fmt == 'single_elim':
        return _undo_se(match)
    return _undo_rr(match)


def _undo_rr(match):
    match.winner = None
    match.save()
    _reopen_if_completed(match.tournament)
    return True, None


def _undo_se(match):
    from .models import Match

    tournament = match.tournament
    next_match = Match.objects.filter(
        tournament=tournament,
        round_number=match.round_number + 1,
        match_number=math.ceil(match.match_number / 2),
    ).first()

    if next_match and next_match.winner_id is not None:
        return False, "Cannot undo — a later round has already been played. Undo that result first."

    if next_match:
        if match.match_number % 2 == 1:
            next_match.player1 = None
        else:
            next_match.player2 = None
        next_match.save()

    match.winner = None
    match.save()
    _reopen_if_completed(tournament)
    return True, None


def _undo_de(match):
    """Reverse a double-elimination result, cascading through auto-bye chains."""
    from .models import Match

    tournament = match.tournament

    # GF round 1: if the LB side won, a bracket-reset match was created.
    bracket_reset = None
    if match.bracket == 'grand_final' and match.round_number == 1:
        bracket_reset = Match.objects.filter(
            tournament=tournament, bracket='grand_final',
            round_number=2, match_number=1,
        ).first()
        if bracket_reset and bracket_reset.winner_id is not None:
            return False, "Cannot undo — bracket-reset match has been played. Undo that first."

    # Walk the routing tree: collect (target_match, slot_to_clear) ops, deepest first.
    # Refuse if any target downstream has a real (non-bye) winner.
    ops = []
    try:
        _collect_de_undo_ops(tournament, match, ops)
    except _UndoBlocked as e:
        return False, f"Cannot undo — {_describe_de_match(e.blocking_match)} has already been played. Undo that result first."

    # Apply: clear slots leaf-to-root. ops is in DFS-post order so cascades land before their root.
    for target, slot in ops:
        target.refresh_from_db()
        if slot == 1:
            target.player1 = None
        else:
            target.player2 = None
        if target.is_bye and target.bracket == 'losers':
            target.is_bye = False
            target.winner = None
        target.save()

    if bracket_reset:
        bracket_reset.delete()

    match.winner = None
    match.save()
    _reopen_if_completed(tournament)
    return True, None


class _UndoBlocked(Exception):
    def __init__(self, blocking_match):
        super().__init__()
        self.blocking_match = blocking_match


def _collect_de_undo_ops(tournament, match, ops):
    for target, slot in _de_routing_targets(tournament, match):
        if target.winner_id is not None and not target.is_bye:
            raise _UndoBlocked(target)
        if target.is_bye and target.winner_id is not None and target.bracket == 'losers':
            # Auto-bye that cascaded further — recurse first so its targets clear before it.
            _collect_de_undo_ops(tournament, target, ops)
        ops.append((target, slot))


def _describe_de_match(match):
    """Human-friendly name like 'WB Quarters M3' or 'LB Round 2 M1'."""
    if match.bracket == 'grand_final':
        return 'the bracket-reset match' if match.round_number == 2 else 'the Grand Final'
    if match.bracket == 'winners':
        wb_rounds = _get_wb_rounds(match.tournament)
        remaining = wb_rounds - match.round_number + 1
        if remaining == 1:
            return 'the WB Final'
        if remaining == 2:
            return f'WB Semis M{match.match_number}'
        if remaining == 3:
            return f'WB Quarters M{match.match_number}'
        return f'WB Round {match.round_number} M{match.match_number}'
    return f'LB Round {match.round_number} M{match.match_number}'


def _de_routing_targets(tournament, match):
    """Where did this match's winner and (if applicable) loser get routed?"""
    from .models import Match

    targets = []
    wb_rounds = _get_wb_rounds(tournament)
    lb_rounds = 2 * (wb_rounds - 1)
    loser = match.player2 if match.winner_id == match.player1_id else match.player1

    if match.bracket == 'winners':
        if match.round_number < wb_rounds:
            t = Match.objects.filter(
                tournament=tournament, bracket='winners',
                round_number=match.round_number + 1,
                match_number=math.ceil(match.match_number / 2),
            ).first()
            slot = 1 if match.match_number % 2 == 1 else 2
        else:
            t = Match.objects.filter(
                tournament=tournament, bracket='grand_final',
                round_number=1, match_number=1,
            ).first()
            slot = 1
        if t:
            targets.append((t, slot))

        if loser and not match.is_bye and lb_rounds > 0:
            if match.round_number == 1:
                lb_m = math.ceil(match.match_number / 2)
                lb_slot = 1 if match.match_number % 2 == 1 else 2
                t = Match.objects.filter(
                    tournament=tournament, bracket='losers',
                    round_number=1, match_number=lb_m,
                ).first()
                if t:
                    targets.append((t, lb_slot))
            else:
                lb_r = 2 * (match.round_number - 1)
                lb_m = _wb_loser_lb_match(match.round_number, match.match_number, wb_rounds)
                t = Match.objects.filter(
                    tournament=tournament, bracket='losers',
                    round_number=lb_r, match_number=lb_m,
                ).first()
                if t:
                    targets.append((t, 2))

    elif match.bracket == 'losers':
        if match.round_number < lb_rounds:
            next_r, next_m, slot = _lb_next_slot(match.round_number, match.match_number)
            t = Match.objects.filter(
                tournament=tournament, bracket='losers',
                round_number=next_r, match_number=next_m,
            ).first()
        else:
            t = Match.objects.filter(
                tournament=tournament, bracket='grand_final',
                round_number=1, match_number=1,
            ).first()
            slot = 2
        if t:
            targets.append((t, slot))

    # GF round 1 → no routing target via this helper; the bracket-reset match
    # is handled separately in _undo_de.
    # GF round 2 → no downstream.

    return targets


def _reopen_if_completed(tournament):
    if tournament.status == 'completed':
        tournament.status = 'active'
        tournament.save()


def get_de_data(tournament):
    """Return structured data for the double elimination bracket template."""
    from collections import defaultdict

    wb = defaultdict(list)
    lb = defaultdict(list)
    gf = []

    for match in tournament.matches.order_by('bracket', 'round_number', 'match_number'):
        if match.bracket == 'winners':
            wb[match.round_number].append(match)
        elif match.bracket == 'losers':
            lb[match.round_number].append(match)
        else:
            gf.append(match)

    wb_count = len(wb)
    lb_count = len(lb)

    wb_rounds = []
    for i in range(1, wb_count + 1):
        remaining = wb_count - i + 1
        if remaining == 1:
            label = 'WB Final'
        elif remaining == 2:
            label = 'WB Semis'
        elif remaining == 3:
            label = 'WB Quarters'
        else:
            label = f'WB Round {i}'
        wb_rounds.append((label, wb[i]))

    lb_rounds = [(f'LB Round {i}', lb[i]) for i in range(1, lb_count + 1)]

    return {
        'wb_rounds': wb_rounds,
        'lb_rounds': lb_rounds,
        'gf_matches': gf,
    }


# ── Round Robin ───────────────────────────────────────────────────────────────

def generate_round_robin(tournament):
    from .models import Match

    tournament.matches.all().delete()

    entries = list(tournament.entries.order_by('seed'))
    n = len(entries)
    if n < 2:
        return

    # Generate all pairs
    match_number = 1
    for i in range(n):
        for j in range(i + 1, n):
            Match.objects.create(
                tournament=tournament,
                round_number=1,
                match_number=match_number,
                player1=entries[i],
                player2=entries[j],
            )
            match_number += 1


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        return f'{n}th'
    return f'{n}{ {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th") }'


def get_se_placements(tournament):
    """
    Ordered list of (label, [entries], start_place) for single-elimination
    placements. start_place is the numeric finish for the first tied entry;
    additional tied entries cover places start_place + 1, start_place + 2, ....
    Only includes the winner (if tournament is complete) and eliminated players.
    Players still alive in the bracket are omitted.
    """
    from collections import defaultdict

    matches_by_round = defaultdict(list)
    for m in tournament.matches.all():
        matches_by_round[m.round_number].append(m)
    if not matches_by_round:
        return []

    max_round = max(matches_by_round.keys())
    results = []

    final = matches_by_round[max_round][0]
    if final.winner_id:
        results.append(('1st', [final.winner], 1))

    for r in sorted(matches_by_round.keys(), reverse=True):
        losers = []
        for m in matches_by_round[r]:
            if m.winner_id and not m.is_bye:
                loser = m.player2 if m.winner_id == m.player1_id else m.player1
                if loser:
                    losers.append(loser)
        if not losers:
            continue
        pos = 2 ** (max_round - r) + 1
        label = _ordinal(pos)
        if len(losers) > 1:
            label = f'T-{label}'
        results.append((label, losers, pos))

    return results


def get_de_placements(tournament):
    """
    Ordered list of (label, [entries], start_place) for double-elimination
    placements. start_place is the numeric finish for the first tied entry;
    additional tied entries cover places start_place + 1, start_place + 2, ....
    Placements are STRUCTURAL — they reserve positions for rounds that haven't
    been played yet, so an LB-R2 loser is shown at T-9th regardless of whether
    the GF or higher LB rounds have been decided.
    """
    wb_rounds = _get_wb_rounds(tournament)
    lb_rounds_total = 2 * (wb_rounds - 1)

    results = []

    gf_r1 = tournament.matches.filter(bracket='grand_final', round_number=1).first()
    gf_r2 = tournament.matches.filter(bracket='grand_final', round_number=2).first()

    winner = None
    runnerup = None
    if gf_r2 and gf_r2.winner_id:
        winner = gf_r2.winner
        runnerup = gf_r2.player2 if gf_r2.winner_id == gf_r2.player1_id else gf_r2.player1
    elif gf_r1 and gf_r1.winner_id and not gf_r2:
        # WB side won outright (no bracket reset created).
        winner = gf_r1.winner
        runnerup = gf_r1.player2 if gf_r1.winner_id == gf_r1.player1_id else gf_r1.player1

    if winner:
        results.append(('1st', [winner], 1))
    if runnerup:
        results.append(('2nd', [runnerup], 2))

    real_slots = _de_real_lb_slots_per_round(tournament)

    # 1st and 2nd are always reserved structurally, played or not.
    above = 2
    for r in range(lb_rounds_total, 0, -1):
        structural_count = real_slots.get(r, 0)
        if structural_count == 0:
            continue

        losers = []
        for m in tournament.matches.filter(bracket='losers', round_number=r):
            if m.winner_id and not m.is_bye:
                loser = m.player2 if m.winner_id == m.player1_id else m.player1
                if loser:
                    losers.append(loser)

        if losers:
            pos = above + 1
            label = _ordinal(pos)
            if structural_count > 1:
                label = f'T-{label}'
            results.append((label, losers, pos))

        above += structural_count

    return results


def _de_real_lb_slots_per_round(tournament):
    """
    For each LB round, count the matches that will actually produce an
    elimination (both slots receive a real player). Auto-byes and double
    phantoms don't count — those positions never resolve into a placement.
    """
    from .models import Match

    wb_byes = _get_wb_byes(tournament)
    counts = {}
    for m in Match.objects.filter(tournament=tournament, bracket='losers'):
        s1_phantom = _lb_slot_is_phantom(tournament, m.round_number, m.match_number, 1, wb_byes)
        s2_phantom = _lb_slot_is_phantom(tournament, m.round_number, m.match_number, 2, wb_byes)
        if not s1_phantom and not s2_phantom:
            counts[m.round_number] = counts.get(m.round_number, 0) + 1
    return counts


def get_round_robin_standings(tournament):
    entries = list(tournament.entries.order_by('seed'))
    stats = {e.id: {'entry': e, 'wins': 0, 'losses': 0, 'played': 0} for e in entries}

    for match in tournament.matches.filter(winner__isnull=False):
        if match.player1_id:
            stats[match.player1_id]['played'] += 1
        if match.player2_id:
            stats[match.player2_id]['played'] += 1
        if match.winner_id:
            stats[match.winner_id]['wins'] += 1
            loser_id = match.player1_id if match.winner_id == match.player2_id else match.player2_id
            if loser_id and loser_id in stats:
                stats[loser_id]['losses'] += 1

    return sorted(stats.values(), key=lambda x: (-x['wins'], x['losses']))


def get_bracket_rounds(tournament):
    """Return matches grouped by round for bracket display."""
    from collections import defaultdict
    rounds = defaultdict(list)
    for match in tournament.matches.order_by('round_number', 'match_number'):
        rounds[match.round_number].append(match)
    return [rounds[r] for r in sorted(rounds.keys())]
