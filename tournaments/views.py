from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Tournament, Player, TournamentEntry, Match
from .forms import RegisterForm, TournamentForm, PlayerForm, AddExistingPlayerForm
from .bracket import (
    generate_single_elimination,
    generate_double_elimination,
    generate_round_robin,
    record_result,
    record_de_result,
    undo_result,
    get_bracket_rounds,
    get_de_data,
    get_round_robin_standings,
    get_se_placements,
    get_de_placements,
)


# ── Auth ─────────────────────────────────────────────────────────────────────

def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f'Welcome, {user.username}!')
            return redirect('home')
    else:
        form = RegisterForm()
    return render(request, 'registration/register.html', {'form': form})


# ── Home ─────────────────────────────────────────────────────────────────────

@login_required
def home(request):
    status_filter = request.GET.get('status', '')
    tournaments = Tournament.objects.select_related('venue', 'created_by')
    if status_filter:
        tournaments = tournaments.filter(status=status_filter)
    return render(request, 'tournaments/home.html', {
        'tournaments': tournaments,
        'status_filter': status_filter,
    })


# ── Tournament CRUD ───────────────────────────────────────────────────────────

@login_required
def create_tournament(request):
    if request.method == 'POST':
        form = TournamentForm(request.POST, request.FILES)
        if form.is_valid():
            tournament = form.save_with_venue(request.user)
            messages.success(request, f'Tournament "{tournament.name}" created!')
            return redirect('tournament_detail', pk=tournament.pk)
    else:
        form = TournamentForm()
    return render(request, 'tournaments/create_tournament.html', {'form': form})


@login_required
@require_POST
def delete_tournament(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    name = tournament.name
    tournament.delete()
    messages.success(request, f'Tournament "{name}" deleted.')
    return redirect('home')


@login_required
def tournament_detail(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    entries = tournament.entries.select_related('player').order_by('seed')

    new_player_form = PlayerForm()
    existing_player_form = AddExistingPlayerForm(user=request.user, tournament=tournament)

    return render(request, 'tournaments/tournament_detail.html', {
        'tournament': tournament,
        'entries': entries,
        'new_player_form': new_player_form,
        'existing_player_form': existing_player_form,
        'can_manage': tournament.created_by == request.user,
    })


# ── Player management ─────────────────────────────────────────────────────────

@login_required
@require_POST
def add_new_player(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    if tournament.status != 'pending':
        messages.error(request, 'Cannot add players to an active or completed tournament.')
        return redirect('tournament_detail', pk=pk)

    form = PlayerForm(request.POST)
    if form.is_valid():
        player, _ = Player.objects.get_or_create(
            name=form.cleaned_data['name'],
            created_by=request.user,
            defaults={
                'email': form.cleaned_data.get('email', ''),
                'phone': form.cleaned_data.get('phone', ''),
            },
        )
        if not TournamentEntry.objects.filter(tournament=tournament, player=player).exists():
            next_seed = tournament.entries.count() + 1
            TournamentEntry.objects.create(tournament=tournament, player=player, seed=next_seed)
            messages.success(request, f'{player.name} added to the tournament.')
        else:
            messages.warning(request, f'{player.name} is already in this tournament.')
    else:
        for error in form.errors.values():
            messages.error(request, error)

    return redirect('tournament_detail', pk=pk)


@login_required
@require_POST
def add_existing_player(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    if tournament.status != 'pending':
        messages.error(request, 'Cannot add players to an active or completed tournament.')
        return redirect('tournament_detail', pk=pk)

    form = AddExistingPlayerForm(request.POST, user=request.user, tournament=tournament)
    if form.is_valid():
        player = form.cleaned_data['player']
        next_seed = tournament.entries.count() + 1
        TournamentEntry.objects.create(tournament=tournament, player=player, seed=next_seed)
        messages.success(request, f'{player.name} added to the tournament.')
    else:
        messages.error(request, 'Could not add player. They may already be in the tournament.')

    return redirect('tournament_detail', pk=pk)


@login_required
@require_POST
def remove_player(request, pk, entry_id):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    if tournament.status != 'pending':
        messages.error(request, 'Cannot remove players from an active tournament.')
        return redirect('tournament_detail', pk=pk)

    entry = get_object_or_404(TournamentEntry, pk=entry_id, tournament=tournament)
    name = entry.player.name
    entry.delete()

    # Re-seed remaining players
    for i, e in enumerate(tournament.entries.order_by('seed'), start=1):
        e.seed = i
        e.save()

    messages.success(request, f'{name} removed.')
    return redirect('tournament_detail', pk=pk)


# ── Tournament flow ───────────────────────────────────────────────────────────

@login_required
@require_POST
def start_tournament(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    if tournament.status != 'pending':
        messages.error(request, 'Tournament has already started.')
        return redirect('tournament_detail', pk=pk)
    if tournament.entries.count() < 2:
        messages.error(request, 'You need at least 2 players to start.')
        return redirect('tournament_detail', pk=pk)

    if tournament.format == 'single_elim':
        generate_single_elimination(tournament)
    elif tournament.format == 'double_elim':
        generate_double_elimination(tournament)
    else:
        generate_round_robin(tournament)

    tournament.status = 'active'
    tournament.save()
    messages.success(request, 'Tournament started!')
    return redirect('bracket', pk=pk)


@login_required
def bracket_view(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if tournament.status == 'pending':
        messages.error(request, 'Tournament has not started yet.')
        return redirect('tournament_detail', pk=pk)

    context = {'tournament': tournament, 'can_manage': tournament.created_by == request.user}

    if tournament.format == 'single_elim':
        rounds = get_bracket_rounds(tournament)
        round_labels = _round_labels(rounds)
        context['bracket'] = list(zip(round_labels, rounds))
        context['placements'] = get_se_placements(tournament)
        return render(request, 'tournaments/bracket.html', context)
    elif tournament.format == 'double_elim':
        context.update(get_de_data(tournament))
        context['placements'] = get_de_placements(tournament)
        return render(request, 'tournaments/double_elimination.html', context)
    else:
        matches = tournament.matches.select_related('player1__player', 'player2__player', 'winner__player')
        standings = get_round_robin_standings(tournament)
        context['matches'] = matches
        context['standings'] = standings
        return render(request, 'tournaments/round_robin.html', context)


def _round_labels(rounds):
    total = len(rounds)
    labels = []
    for i, _ in enumerate(rounds):
        remaining = total - i
        if remaining == 1:
            labels.append('Final')
        elif remaining == 2:
            labels.append('Semifinals')
        elif remaining == 3:
            labels.append('Quarterfinals')
        else:
            labels.append(f'Round {i + 1}')
    return labels


@login_required
@require_POST
def record_match_result(request, pk, match_id):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    match = get_object_or_404(Match, pk=match_id, tournament=tournament)

    winner_id = request.POST.get('winner_id')
    if not winner_id:
        messages.error(request, 'No winner selected.')
        return redirect('bracket', pk=pk)

    entry = get_object_or_404(TournamentEntry, pk=winner_id)
    if entry not in [match.player1, match.player2]:
        messages.error(request, 'Invalid winner.')
        return redirect('bracket', pk=pk)

    if tournament.format == 'double_elim':
        record_de_result(match, entry)
    else:
        record_result(match, entry)

    if tournament.status == 'completed':
        messages.success(request, f'Tournament complete! Winner: {entry.player.name}')
    else:
        messages.success(request, f'{entry.player.name} advances!')

    return redirect('bracket', pk=pk)


@login_required
@require_POST
def undo_match_result(request, pk, match_id):
    tournament = get_object_or_404(Tournament, pk=pk, created_by=request.user)
    match = get_object_or_404(Match, pk=match_id, tournament=tournament)

    success, error = undo_result(match)
    if success:
        messages.success(request, 'Result undone.')
    else:
        messages.error(request, error or 'Could not undo result.')

    if tournament.format == 'round_robin' or tournament.status != 'pending':
        return redirect('bracket', pk=pk)
    return redirect('tournament_detail', pk=pk)
