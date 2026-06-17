from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User


GAME_CHOICES = [
    ('8ball', '8-Ball'),
    ('9ball', '9-Ball'),
    ('10ball', '10-Ball'),
    ('straight', 'Straight Pool'),
    ('one_pocket', 'One Pocket'),
    ('bank', 'Bank Pool'),
]

FORMAT_CHOICES = [
    ('single_elim', 'Single Elimination'),
    ('double_elim', 'Double Elimination'),
    ('round_robin', 'Round Robin'),
]

STATUS_CHOICES = [
    ('pending', 'Registration Open'),
    ('active', 'In Progress'),
    ('completed', 'Completed'),
]


class Venue(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=50)
    zip_code = models.CharField(max_length=10, blank=True)

    def __str__(self):
        return f"{self.name} — {self.city}, {self.state}"

    class Meta:
        ordering = ['name']


class Tournament(models.Model):
    name = models.CharField(max_length=200)
    game_type = models.CharField(max_length=20, choices=GAME_CHOICES)
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default='single_elim')
    flyer = models.ImageField(upload_to='flyers/', blank=True, null=True)
    venue = models.ForeignKey(Venue, on_delete=models.SET_NULL, null=True, blank=True, related_name='tournaments')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tournaments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    date = models.DateTimeField(null=True, blank=True)
    entry_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    added_money = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def player_count(self):
        return self.entries.count()

    def get_game_display_name(self):
        return dict(GAME_CHOICES).get(self.game_type, self.game_type)

    def get_format_display_name(self):
        return dict(FORMAT_CHOICES).get(self.format, self.format)

    def total_pot(self):
        fee = self.entry_fee or Decimal('0')
        added = self.added_money or Decimal('0')
        return (fee * self.player_count()) + added

    class Meta:
        ordering = ['-created_at']


class Player(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='players')

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class TournamentEntry(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='entries')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='entries')
    seed = models.IntegerField(default=0)
    checked_in = models.BooleanField(default=False)

    class Meta:
        unique_together = ('tournament', 'player')
        ordering = ['seed']

    def __str__(self):
        return f"{self.player.name} (#{self.seed}) — {self.tournament.name}"


BRACKET_CHOICES = [
    ('winners', 'Winners'),
    ('losers', 'Losers'),
    ('grand_final', 'Grand Final'),
]


class Match(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='matches')
    bracket = models.CharField(max_length=20, choices=BRACKET_CHOICES, default='winners')
    round_number = models.IntegerField()
    match_number = models.IntegerField()
    player1 = models.ForeignKey(
        TournamentEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_as_p1'
    )
    player2 = models.ForeignKey(
        TournamentEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_as_p2'
    )
    winner = models.ForeignKey(
        TournamentEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='matches_won'
    )
    is_bye = models.BooleanField(default=False)

    class Meta:
        ordering = ['round_number', 'match_number']
        unique_together = ('tournament', 'bracket', 'round_number', 'match_number')

    def __str__(self):
        return f"R{self.round_number}M{self.match_number} — {self.tournament.name}"

    def is_complete(self):
        return self.winner is not None

    def is_ready(self):
        return self.player1 is not None and self.player2 is not None and self.winner is None


class Payout(models.Model):
    PAYOUT_TYPES = [('flat', 'Flat Rate'), ('percentage', 'Percentage')]

    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='payouts')
    place = models.PositiveIntegerField()
    payout_type = models.CharField(max_length=10, choices=PAYOUT_TYPES)
    amount = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        unique_together = ('tournament', 'place')
        ordering = ['place']

    def __str__(self):
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(self.place, 'th')
        return f"{self.place}{suffix} place — {self.tournament.name}"

    def calculated_amount(self, total_pot):
        if self.payout_type == 'flat':
            return self.amount
        return (self.amount / Decimal('100')) * total_pot
