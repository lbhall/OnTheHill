from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Tournament, Venue, Player, TournamentEntry, Payout


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']


class VenueForm(forms.ModelForm):
    class Meta:
        model = Venue
        fields = ['name', 'address', 'city', 'state', 'zip_code']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Corner Pocket Billiards'}),
            'address': forms.TextInput(attrs={'placeholder': '123 Main St'}),
            'city': forms.TextInput(attrs={'placeholder': 'City'}),
            'state': forms.TextInput(attrs={'placeholder': 'State'}),
            'zip_code': forms.TextInput(attrs={'placeholder': 'ZIP'}),
        }


class TournamentForm(forms.ModelForm):
    # Venue fields (used when creating a new venue inline)
    venue_name = forms.CharField(max_length=200, required=False, label='Venue Name',
                                 widget=forms.TextInput(attrs={'placeholder': 'e.g. Corner Pocket Billiards'}))
    venue_address = forms.CharField(max_length=300, required=False, label='Address',
                                    widget=forms.TextInput(attrs={'placeholder': '123 Main St'}))
    venue_city = forms.CharField(max_length=100, required=False, label='City')
    venue_state = forms.CharField(max_length=50, required=False, label='State')
    venue_zip = forms.CharField(max_length=10, required=False, label='ZIP Code')

    date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        input_formats=['%Y-%m-%d', '%m/%d/%Y'],
    )

    class Meta:
        model = Tournament
        fields = ['name', 'game_type', 'format', 'date', 'entry_fee', 'added_money', 'flyer', 'venue', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Tournament name'}),
            'entry_fee': forms.NumberInput(attrs={'placeholder': '0.00', 'step': '0.01'}),
            'added_money': forms.NumberInput(attrs={'placeholder': '0.00', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Optional notes or rules...'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['venue'].queryset = Venue.objects.all()
        self.fields['venue'].required = False
        self.fields['venue'].empty_label = '— Create new venue below —'
        # Pre-populate date field from existing instance (DateTimeField → date)
        if self.instance and self.instance.pk and self.instance.date:
            self.initial['date'] = self.instance.date.date()

    def clean_date(self):
        from datetime import datetime, time as dtime
        d = self.cleaned_data.get('date')
        if d:
            return datetime.combine(d, dtime(12, 0))
        return None

    def clean(self):
        cleaned_data = super().clean()
        venue = cleaned_data.get('venue')
        venue_name = cleaned_data.get('venue_name', '').strip()

        # If no existing venue selected and a new venue name was provided, require city/state
        if not venue and venue_name:
            if not cleaned_data.get('venue_city', '').strip():
                self.add_error('venue_city', 'City is required for new venue.')
            if not cleaned_data.get('venue_state', '').strip():
                self.add_error('venue_state', 'State is required for new venue.')

        return cleaned_data

    def save_with_venue(self, user, commit=True):
        tournament = self.save(commit=False)
        tournament.created_by = user

        venue = self.cleaned_data.get('venue')
        venue_name = self.cleaned_data.get('venue_name', '').strip()

        if not venue and venue_name:
            venue, _ = Venue.objects.get_or_create(
                name=venue_name,
                city=self.cleaned_data.get('venue_city', ''),
                state=self.cleaned_data.get('venue_state', ''),
                defaults={
                    'address': self.cleaned_data.get('venue_address', ''),
                    'zip_code': self.cleaned_data.get('venue_zip', ''),
                },
            )

        tournament.venue = venue

        if commit:
            tournament.save()
        return tournament


class PlayerForm(forms.ModelForm):
    class Meta:
        model = Player
        fields = ['name', 'email', 'phone']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Full name'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email (optional)'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Phone (optional)'}),
        }


class AddExistingPlayerForm(forms.Form):
    player = forms.ModelChoiceField(
        queryset=Player.objects.none(),
        label='Select existing player',
        empty_label='— Choose a player —',
    )

    def __init__(self, *args, user=None, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and tournament:
            already_in = tournament.entries.values_list('player_id', flat=True)
            self.fields['player'].queryset = Player.objects.filter(
                created_by=user
            ).exclude(id__in=already_in)


class PayoutForm(forms.ModelForm):
    class Meta:
        model = Payout
        fields = ['place', 'payout_type', 'amount']
        widgets = {
            'place': forms.NumberInput(attrs={'placeholder': '1', 'min': '1'}),
            'amount': forms.NumberInput(attrs={'placeholder': '0.00', 'step': '0.01', 'min': '0'}),
        }
