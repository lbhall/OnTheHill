import base64
import binascii
import json
import secrets
from datetime import datetime, time as dtime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    ApiToken,
    GAME_CHOICES,
    FORMAT_CHOICES,
    Payout,
    Player,
    Tournament,
    TournamentEntry,
    Venue,
)


def _authenticate(request):
    """Resolve `Authorization: Token <hex>` to a User, or return None."""
    header = request.META.get('HTTP_AUTHORIZATION', '')
    prefix = 'Token '
    if not header.startswith(prefix):
        return None
    key = header[len(prefix):].strip()
    if not key:
        return None
    try:
        token = ApiToken.objects.select_related('user').get(key=key)
    except ApiToken.DoesNotExist:
        return None
    token.last_used_at = timezone.now()
    token.save(update_fields=['last_used_at'])
    return token.user


def _err(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _parse_decimal(value, field):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f'{field} must be a number')


VALID_GAMES = {c[0] for c in GAME_CHOICES}
VALID_FORMATS = {c[0] for c in FORMAT_CHOICES}
VALID_PAYOUT_TYPES = {c[0] for c in Payout.PAYOUT_TYPES}


@csrf_exempt
@require_POST
def create_tournament(request):
    user = _authenticate(request)
    if not user:
        return _err('Invalid or missing API token. Send `Authorization: Token <hex>`.', status=401)

    try:
        data = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return _err('Request body must be valid JSON.')
    if not isinstance(data, dict):
        return _err('Request body must be a JSON object.')

    name = (data.get('name') or '').strip()
    if not name:
        return _err('`name` is required.')

    game_type = data.get('game_type', '8ball')
    if game_type not in VALID_GAMES:
        return _err(f'`game_type` must be one of: {sorted(VALID_GAMES)}.')

    fmt = data.get('format', 'single_elim')
    if fmt not in VALID_FORMATS:
        return _err(f'`format` must be one of: {sorted(VALID_FORMATS)}.')

    date_str = data.get('date')
    date_value = None
    if date_str:
        try:
            parsed = datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            return _err('`date` must be in YYYY-MM-DD format.')
        date_value = datetime.combine(parsed.date(), dtime(12, 0))

    try:
        entry_fee = _parse_decimal(data.get('entry_fee'), 'entry_fee')
        added_money = _parse_decimal(data.get('added_money'), 'added_money') or Decimal('0')
    except ValueError as e:
        return _err(str(e))

    venue = None
    venue_id = data.get('venue_id')
    if venue_id is not None:
        try:
            venue = Venue.objects.get(pk=venue_id)
        except Venue.DoesNotExist:
            return _err(f'Venue {venue_id} not found.')

    teams = data.get('teams', [])
    if not isinstance(teams, list):
        return _err('`teams` must be a list.')

    cleaned_teams = []
    seen = set()
    for i, item in enumerate(teams):
        if isinstance(item, str):
            tname, email, phone = item.strip(), '', ''
        elif isinstance(item, dict):
            tname = (item.get('name') or '').strip()
            email = item.get('email') or ''
            phone = item.get('phone') or ''
        else:
            return _err(f'teams[{i}] must be a string or object.')
        if not tname:
            return _err(f'teams[{i}] has no name.')
        key = tname.lower()
        if key in seen:
            return _err(f'Duplicate team name: {tname!r}.')
        seen.add(key)
        cleaned_teams.append({'name': tname, 'email': email, 'phone': phone})

    notes = data.get('notes', '') or ''

    with transaction.atomic():
        tournament = Tournament.objects.create(
            name=name,
            game_type=game_type,
            format=fmt,
            date=date_value,
            entry_fee=entry_fee,
            added_money=added_money,
            venue=venue,
            notes=notes,
            created_by=user,
        )

        entries_out = []
        for i, t in enumerate(cleaned_teams):
            player, _created = Player.objects.get_or_create(
                name=t['name'],
                created_by=user,
                defaults={'email': t['email'], 'phone': t['phone']},
            )
            entry = TournamentEntry.objects.create(
                tournament=tournament,
                player=player,
                seed=i + 1,
            )
            entries_out.append({
                'id': entry.id,
                'name': player.name,
                'seed': entry.seed,
            })

    return JsonResponse({
        'id': tournament.id,
        'name': tournament.name,
        'game_type': tournament.game_type,
        'format': tournament.format,
        'status': tournament.status,
        'entries': entries_out,
        'url': request.build_absolute_uri(f'/tournaments/{tournament.id}/'),
    }, status=201)


@csrf_exempt
@require_POST
def add_payout(request, pk):
    user = _authenticate(request)
    if not user:
        return _err('Invalid or missing API token. Send `Authorization: Token <hex>`.', status=401)

    try:
        tournament = Tournament.objects.get(pk=pk, created_by=user)
    except Tournament.DoesNotExist:
        return _err(f'Tournament {pk} not found.', status=404)

    if tournament.status == 'completed':
        return _err('Cannot edit payouts on a completed tournament.')

    try:
        data = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return _err('Request body must be valid JSON.')
    if not isinstance(data, dict):
        return _err('Request body must be a JSON object.')

    place = data.get('place')
    try:
        place = int(place)
        if place < 1:
            raise ValueError
    except (TypeError, ValueError):
        return _err('`place` must be a positive integer.')

    payout_type = data.get('payout_type')
    if payout_type not in VALID_PAYOUT_TYPES:
        return _err(f'`payout_type` must be one of: {sorted(VALID_PAYOUT_TYPES)}.')

    try:
        amount = _parse_decimal(data.get('amount'), 'amount')
    except ValueError as e:
        return _err(str(e))
    if amount is None or amount < 0:
        return _err('`amount` is required and cannot be negative.')

    if Payout.objects.filter(tournament=tournament, place=place).exists():
        return _err(f'A payout for place #{place} already exists.', status=409)

    payout = Payout.objects.create(
        tournament=tournament,
        place=place,
        payout_type=payout_type,
        amount=amount,
    )

    return JsonResponse({
        'id': payout.id,
        'tournament_id': tournament.id,
        'place': payout.place,
        'payout_type': payout.payout_type,
        'amount': str(payout.amount),
    }, status=201)


@csrf_exempt
@require_POST
def obtain_token(request):
    try:
        data = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return _err('Request body must be valid JSON.')
    if not isinstance(data, dict):
        return _err('Request body must be a JSON object.')

    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return _err('`username` and `password` are required.')

    user = authenticate(request, username=username, password=password)
    if user is None:
        return _err('Invalid credentials.', status=401)

    token = ApiToken.objects.filter(user=user).first()
    if token is None:
        token = ApiToken.generate_for(user)

    return JsonResponse({'token': token.key})
