"""
Recherche sans tenir compte des accents, accélérée par des index trigrammes.

La fonction unaccent de PostgreSQL n'est pas déclarée « immuable » : elle ne peut pas servir dans un
index.
f_unaccent (migration 0028) l'enveloppe en fonction immuable ; « champ__sans_accent__icontains »
produit
UPPER(f_unaccent(champ)) LIKE …, exactement l'expression indexée.
"""

from django.db.models import CharField, TextField, Transform


class SansAccent(Transform):
    bilateral = True  # le texte cherché est lui aussi débarrassé de ses accents
    lookup_name = "sans_accent"
    function = "f_unaccent"


CharField.register_lookup(SansAccent)
TextField.register_lookup(SansAccent)
