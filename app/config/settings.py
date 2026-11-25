"""
Configuration Django de CyberLearning.

Tous les secrets et paramètres propres à l'environnement viennent de variables
d'environnement (fichier .env, jamais commité). Les valeurs par défaut sont
volontairement sûres : DEBUG désactivé, cookies sécurisés, etc.
"""
from pathlib import Path
import os
import sys

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"La variable d'environnement {name} est obligatoire.")
    return value


def env_bool(name, default=False):
    return str(env(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --- Cœur -------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost")
# 127.0.0.1 est toujours accepté pour le healthcheck interne du conteneur.
if "127.0.0.1" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append("127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # En développement, WhiteNoise sert aussi les fichiers statiques (à la place de runserver) avec
    # « max-age=0 » : le navigateur revérifie app.js / app.css et ne garde pas une ancienne version.
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # recherche insensible aux accents (unaccent)
    "axes",  # protection contre le brute-force sur la connexion
    "accounts",
    "catalogue",
    "progression",
    "social",
    "courses",
    "podcasts",
    "studio",
]
# Apps privées : présentes dans le dépôt de l'éditeur, absentes du dépôt public (scripts/publier-public.sh).
INSTALLED_APPS += [app for app in ("anssi",) if (BASE_DIR / app).is_dir()]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.SecurityMiddleware",  # double authentification, inactivité, CSP
    "catalogue.middleware.AIAvailabilityMiddleware",  # installation sans IA : générations refusées
    "axes.middleware.AxesMiddleware",  # doit rester en dernier
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.instance",
                "progression.context_processors.progression",
                "social.context_processors.friend_requests",
                "catalogue.context_processors.review",
                "catalogue.context_processors.assistant",
                "catalogue.context_processors.navigation",
            ],
        },
    },
]

# --- Base de données ----------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "cyberlearning"),
        # Rôle applicatif sans droits d'administration (install.sh) ; à défaut, le super-utilisateur
        # (installations antérieures).
        "USER": env("DB_USER") or env("POSTGRES_USER", "cyberlearning"),
        "PASSWORD": env("DB_PASSWORD") or env("POSTGRES_PASSWORD", required=True),
        "HOST": env("POSTGRES_HOST", "db"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        # Base gérée dans le cloud : DB_SSLMODE=verify-full (et POSTGRES_HOST vers le service).
        "OPTIONS": {"sslmode": env("DB_SSLMODE", "prefer")},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cache partagé par tous les processus (table PostgreSQL, sans service de plus) : limites de débit.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "cache_partage",
    }
}

# --- Authentification ---------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "catalogue:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Argon2id en premier : algorithme recommandé pour le stockage des mots de passe.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# django-axes : verrouillage après 5 échecs pour un couple pseudo + IP, 30 min.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.5  # heures
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "config.client_ip.get_client_ip"

# Chemin de l'interface d'administration (éviter le /admin/ par défaut).
ADMIN_URL_PATH = env("DJANGO_ADMIN_URL_PATH", "gestion/").strip("/") + "/"

# Ouverture des inscriptions (fermées par défaut : comptes créés par un administrateur ;
# SIGNUP_ENABLED=1 pour ouvrir).
SIGNUP_ENABLED = env_bool("SIGNUP_ENABLED", False)

# Double authentification obligatoire pour les administrateurs, gestionnaires et rédacteurs
# (facultative sinon),
# et déconnexion de ces comptes après une période d'inactivité.
MFA_REQUIRED_FOR_PRIVILEGED = env_bool("MFA_REQUIRED_FOR_PRIVILEGED", True)
PRIVILEGED_IDLE_MINUTES = int(env("PRIVILEGED_IDLE_MINUTES", "30"))
# Conservation du journal d'audit (jours) ; les autres durées sont dans « manage.py purge_donnees ».
AUDIT_RETENTION_DAYS = int(env("AUDIT_RETENTION_DAYS", "365"))

# --- Sessions, cookies, en-têtes ---------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 heures
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

# En production (HTTPS), ces options doivent rester à True.
SECURE_COOKIES = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)  # nginx s'en charge
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))  # activer une fois HTTPS validé
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- E-mails (invitations, mot de passe oublié) ------------------------------------------------
# Sans EMAIL_HOST, aucun e-mail n'est envoyé : l'administrateur copie les liens d'invitation
# lui-même et
# « Mot de passe oublié » n'est pas proposé.
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_ENABLED = bool(EMAIL_HOST)
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if EMAIL_ENABLED
                 else "django.core.mail.backends.dummy.EmailBackend")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", EMAIL_PORT == 587)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", EMAIL_PORT == 465)
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", f"CyberLearning <no-reply@{ALLOWED_HOSTS[0]}>")
PASSWORD_RESET_TIMEOUT = (
    3 * 24 * 3600
)  # liens d'invitation et de réinitialisation : 3 jours, un seul usage

# Version de l'application (fichier app/VERSION, mis à jour à chaque publication : voir docs/).
APP_VERSION = (
    (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    if (BASE_DIR / "VERSION").exists()
    else "dev"
)

# --- Instance (une installation par client, sur site ou dans le cloud) -------------------------
# Nom affiché, éditeur de l'instance et contacts : repris dans l'en-tête, le pied de page et les
# pages légales.
INSTANCE_NAME = env("INSTANCE_NAME", "CyberLearning")
ORGANISATION_NAME = env("ORGANISATION_NAME", "")          # raison sociale de l'exploitant
ORGANISATION_ADDRESS = env("ORGANISATION_ADDRESS", "")
ORGANISATION_ID = env("ORGANISATION_ID", "")              # SIREN / RCS
CONTACT_EMAIL = env("CONTACT_EMAIL", "")
DPO_EMAIL = env("DPO_EMAIL", "")  # délégué à la protection des données (à défaut : CONTACT_EMAIL)
HOSTING_PROVIDER = env(
    "HOSTING_PROVIDER", ""
)  # hébergeur (cloud) ou « serveurs de l'organisation » (sur site)
# Programmes d'examen des certifications tierces (ISC2, ISACA, CompTIA…) : domaines et pondérations
# publiés par
# leurs détenteurs. 0 pour les masquer (demande d'un client, d'un juriste ou d'un détenteur de
# marque).
THIRD_PARTY_EXAM_OUTLINES = env_bool("THIRD_PARTY_EXAM_OUTLINES", True)

# --- Internationalisation -----------------------------------------------------
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

# --- Fichiers statiques et médias ------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

# IA locale (Ollama : QCM, cours, podcasts, lexiques, assistant ; synthèse vocale : versions audio,
# podcasts).
# 0 sur une installation sans carte graphique : l'application fonctionne, les fonctions d'IA sont
# masquées.
AI_ENABLED = env_bool("AI_ENABLED", True)
# Pré-relecteur IA (catalogue/prereview.py) : contrôle des QCM et des cours générés avant la
# relecture humaine.
# 0 pour le couper sur cette installation ; il peut aussi être coupé depuis le tableau de bord des
# générations.
PREREVIEW_ENABLED = env_bool("PREREVIEW_ENABLED", True)

# Version audio des guides : service de synthèse vocale Piper (conteneur « tts », réseau interne).
NARRATION_TTS_URL = env("NARRATION_TTS_URL", "http://tts:8000")

# Génération des QCM par IA locale (Ollama, profil Docker « ia ») ; relecture humaine avant
# publication.
QUIZ_LLM_URL = env("QUIZ_LLM_URL", "http://ollama:11434")
# Banc d'essai PASSI (oct. 2026) : gemma3:12b 2× plus rapide que qwen3:14b, sans doublon ;
# qwen3.5:9b échouait une série.
QUIZ_LLM_MODEL = env("QUIZ_LLM_MODEL", "gemma3:12b")
# Avis du pré-relecteur : gemma3:12b juge tout « bonne », qwen3:14b repère les douteuses.
PREREVIEW_LLM_MODEL = env("PREREVIEW_LLM_MODEL", "qwen3:14b")
QUIZ_LLM_CONTEXT = int(env("QUIZ_LLM_CONTEXT", "8192"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
# Pas d'erreur si le manifeste est absent (tests, développement sans collectstatic).
WHITENOISE_MANIFEST_STRICT = False
# Les tests tournent sans collectstatic : stockage simple, sans manifeste.
if "test" in sys.argv[1:2]:
    STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
    # Les tests existants se connectent sans second facteur ; ceux de la double authentification
    # l'activent.
    MFA_REQUIRED_FOR_PRIVILEGED = False
    # Mêmes résultats sur le poste et dans la CI, quel que soit le .env : valeur par défaut de
    # production.
    SIGNUP_ENABLED = False
    # Les générations simulées des tests existants ne prévoient pas la pré-relecture ; ses propres
    # tests l'activent.
    PREREVIEW_ENABLED = False

# Taille maximale des PDF de guides téléversés (Mo).
GUIDE_MAX_UPLOAD_MB = int(env("GUIDE_MAX_UPLOAD_MB", "50"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# --- Journalisation -----------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "audit": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Avertissements de pdfminer sur des couleurs mal formées : sans effet sur le texte extrait.
        "pdfminer": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}
