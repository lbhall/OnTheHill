from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('register/', views.register_view, name='register'),

    path('tournaments/create/', views.create_tournament, name='create_tournament'),
    path('tournaments/<int:pk>/', views.tournament_detail, name='tournament_detail'),
    path('tournaments/<int:pk>/delete/', views.delete_tournament, name='delete_tournament'),
    path('tournaments/<int:pk>/add-new-player/', views.add_new_player, name='add_new_player'),
    path('tournaments/<int:pk>/add-existing-player/', views.add_existing_player, name='add_existing_player'),
    path('tournaments/<int:pk>/remove-player/<int:entry_id>/', views.remove_player, name='remove_player'),
    path('tournaments/<int:pk>/start/', views.start_tournament, name='start_tournament'),
    path('tournaments/<int:pk>/bracket/', views.bracket_view, name='bracket'),
    path('tournaments/<int:pk>/result/<int:match_id>/', views.record_match_result, name='record_match_result'),
    path('tournaments/<int:pk>/undo/<int:match_id>/', views.undo_match_result, name='undo_match_result'),
]
