"""
Boutique d'avatars : des personnages (emoji) sur des fonds de couleur, achetés avec des émeraudes.

Chaque rareté se débloque à partir d'un niveau : plus on progresse, plus la
boutique s'agrandit. Un avatar peut exiger un niveau plus élevé que sa rareté.

Le catalogue est défini ici, dans le code : ajouter un avatar ne demande aucune
migration. La clé (`key`) est enregistrée dans la base : ne jamais la renommer.

Pas de téléversement d'images : rien à analyser ni à stocker, aucun risque de
fichier malveillant ou de contenu inapproprié.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rarity:
    key: str
    label: str
    price: int       # en émeraudes
    min_level: int   # niveau à partir duquel la rareté est en vente


FREE = Rarity("gratuit", "Offert", 0, 1)
COMMON = Rarity("commun", "Commun", 20, 2)
RARE = Rarity("rare", "Rare", 50, 3)
EPIC = Rarity("epique", "Épique", 120, 5)
LEGENDARY = Rarity("legendaire", "Légendaire", 300, 7)
RARITIES = [FREE, COMMON, RARE, EPIC, LEGENDARY]


@dataclass(frozen=True)
class Avatar:
    key: str
    name: str
    emoji: str
    background: str  # classe CSS « avatar-bg-… »
    rarity: Rarity
    level: int = 0  # niveau exigé s'il dépasse celui de la rareté

    @property
    def price(self):
        return self.rarity.price

    @property
    def min_level(self):
        return max(self.level, self.rarity.min_level)


AVATARS = [
    # Offerts à tous
    Avatar("bouclier", "Bouclier", "🛡️", "blue", FREE),
    Avatar("cadenas", "Cadenas", "🔐", "teal", FREE),
    Avatar("clavier", "Clavier", "⌨️", "slate", FREE),
    # Communs
    Avatar("pingouin", "Manchot libre", "🐧", "sky", COMMON),
    Avatar("chouette", "Chouette de veille", "🦉", "green", COMMON),
    Avatar("cle", "Clé maîtresse", "🗝️", "gold", COMMON),
    Avatar("satellite", "Antenne", "📡", "purple", COMMON),
    Avatar("loupe", "Enquêteur", "🔍", "orange", COMMON),
    Avatar("disquette", "Sauvegarde", "💾", "slate", COMMON),
    # Rares
    Avatar("renard", "Renard rusé", "🦊", "orange", RARE),
    Avatar("robot", "Robot d'analyse", "🤖", "teal", RARE),
    Avatar("detective", "Détective", "🕵️", "slate", RARE),
    Avatar("abeille", "Abeille de détection", "🐝", "gold", RARE),
    Avatar("poulpe", "Poulpe multi-tâches", "🐙", "pink", RARE),
    # Épiques
    Avatar("ninja", "Ninja", "🥷", "dark", EPIC),
    Avatar("envahisseur", "Envahisseur", "👾", "purple", EPIC),
    Avatar("loup", "Loup solitaire", "🐺", "night", EPIC),
    Avatar("aigle", "Aigle", "🦅", "sky", EPIC),
    # Légendaires
    Avatar("dragon", "Dragon gardien", "🐉", "cyber", LEGENDARY),
    Avatar("mage", "Mage du chiffrement", "🧙", "aurora", LEGENDARY),
    Avatar("licorne", "Licorne zero-day", "🦄", "aurora", LEGENDARY),
    Avatar("pirate", "Red team", "🏴‍☠️", "cyber", LEGENDARY, level=9),
]
BY_KEY = {avatar.key: avatar for avatar in AVATARS}


def get(key):
    return BY_KEY.get(key)
