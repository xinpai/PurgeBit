# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2008-2026 Andrew Ziem.
#
# This work is licensed under the terms of the GNU GPL, version 3 or
# later.  See the COPYING file in the top-level directory.
#
# Modified by PurgeBit contributors, 2026-10-02.

import gettext
import locale
import os
import logging

from bleachbit import IS_MAC, IS_POSIX, IS_WINDOWS

logger = logging.getLogger(__name__)

# PurgeBit: the interface language used when auto-detection is disabled and
# the user has not chosen a language.  Simplified Chinese is the primary
# language of this distribution.
DEFAULT_LANGUAGE = 'zh_CN'


class LocaleCode:
    """Parsed locale name like 'de_DE.ISO8859-15@euro'.

    Locale names have the shape language[_territory][.codeset][@modifier].
    Missing pieces are None, not empty strings.
    """

    __slots__ = ('raw', 'language', 'territory', 'encoding', 'modifier')

    def __init__(self, raw):
        # Split '@' before '.', because a modifier may itself contain a
        # dot ('en@boldquot.header' in locale.alias).
        pre, _, modifier = raw.partition('@')
        code, _, encoding = pre.partition('.')
        language, _, territory = code.replace('-', '_').partition('_')
        self.raw = raw
        self.language = language
        self.territory = territory or None
        self.encoding = encoding or None
        self.modifier = modifier or None

    def __str__(self):
        return self.raw

    @property
    def normalized(self):
        """Return 'de_DE': language[_territory], without codeset or modifier."""
        if self.territory:
            return f'{self.language}_{self.territory}'
        return self.language

    @property
    def is_utf8(self):
        """Return whether the locale name specifies a UTF-8 codeset.

        Compared case- and punctuation-insensitively because `locale -a`
        prints '.utf8' on Linux and '.UTF-8' on macOS. A name without a
        codeset is not assumed to be UTF-8.
        """
        if not self.encoding:
            return False
        return self.encoding.replace(
            '-', '').replace('_', '').lower() == 'utf8'

    @property
    def is_special(self):
        """Return whether this is C/POSIX (with optional codeset/modifier)."""
        return self.language in ('C', 'POSIX') and self.territory is None


# PurgeBit: this distribution ships only Chinese and English translations,
# so only those names are listed here.  The dictionary maps a language code
# to its name in its own language, as shown in the language chooser.
native_locale_names = \
    {'en': 'English',
     'en_AU': 'Australian English',
     'en_CA': 'Canadian English',
     'en_GB': 'British English',
     'en_US': 'United States English',
     'zh': '中文',
     'zh_CN': '简体中文',
     'zh_TW': '繁體中文'}


def get_supported_language_codes():
    """Return list of supported languages as language codes

    Supported means a translation may be available.
    """
    supported_langs = []
    # Use local import to avoid circular import.
    from bleachbit import locale_dir
    # The locale_dir may not exist, especially on Windows.
    if not os.path.isdir(locale_dir):
        return ['en_US', 'en']
    lang_codes = sorted(set(os.listdir(locale_dir) + ['en_US', 'en']))
    for lang in lang_codes:
        if lang in ('en', 'en_US'):
            supported_langs.append(lang)
            continue
        if os.path.isdir(os.path.join(locale_dir, lang)):
            try:
                translation = gettext.translation(
                    'bleachbit', locale_dir, languages=[lang])
                if translation:
                    supported_langs.append(lang)
            except FileNotFoundError:
                logger.debug('no compiled translation for language %s', lang)
    return supported_langs


def get_supported_language_code_name_dict():
    """Return dictionary of supported languages as language codes and names

    Supported means a translation is available.
    """
    supported_langs = {}
    for lang in get_supported_language_codes():
        supported_langs[lang] = native_locale_names.get(lang, None)
    return supported_langs


def find_supported_language_code(lang_code, supported_codes):
    """Return the supported language code best matching lang_code.

    lang_code is a detected code like 'en_US' or 'hi_IN', and
    supported_codes is an iterable of codes like 'en', 'en_US', 'hi'.

    For example, 'hi_IN' matches 'hi', 'en-US' matches 'en_US', and
    'pt' may match 'pt_BR'.

    Returns None if there is no match.
    """
    if not lang_code:
        return None
    loc = LocaleCode(lang_code)
    if not loc.language or loc.is_special or len(loc.language) < 2:
        return None
    codes = list(supported_codes)
    # Try the full code ('hi_IN'), then the primary subtag ('hi').
    lowered = {code.lower(): code for code in codes}
    for candidate in (lang_code, loc.normalized, loc.language):
        if candidate in codes:
            return candidate
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    # Try a supported regional variant like 'pt_BR' for 'pt'. Prefer the
    # variant whose region is implied by the detected code's region or
    # script subtags: 'zh_Hant_TW', 'zh_Hant', and 'zh_HK' all imply
    # Traditional Chinese, so they match 'zh_TW' rather than 'zh_CN'.
    subtags = set(loc.normalized.lower().split('_')[1:])
    if subtags & {'hant', 'tw', 'hk', 'mo'}:
        preferred_region = 'tw'
    elif loc.territory:
        preferred_region = loc.territory.lower().rsplit('_', 1)[-1]
    else:
        preferred_region = None
    prefix = loc.language.lower() + '_'
    first_match = None
    for code in codes:
        if not code.lower().startswith(prefix):
            continue
        if first_match is None:
            first_match = code
        if preferred_region and code.lower() == prefix + preferred_region:
            return code
    return first_match


_UNSET = object()

# locale.getlocale() reports whatever setlocale() set, so the real system
# locale is captured here before setup_translation() forces a language.
_locale_fallback = _UNSET


def _get_locale_fallback():
    """Return the system locale as seen before setup_translation() ran."""
    # Captured once per process.
    # pylint: disable-next=global-statement
    global _locale_fallback
    if _locale_fallback is _UNSET:
        # locale.getlocale() can raise ValueError for a locale name it
        # cannot normalize.
        try:
            _locale_fallback = locale.getlocale()[0]
        except ValueError:
            _locale_fallback = None
    return _locale_fallback


def normalize_locale_code(code):
    """Return the language code normalized like 'en_US'.

    Strips the codeset ('.UTF-8') and modifier ('@latin'), and converts
    hyphens to underscores, so a BCP 47 code like 'en-US' matches the
    underscore form used in locale directory names.
    """
    return LocaleCode(code).normalized


def get_active_language_code():
    """Return the language ID to use for translations

    The language ID is a code like: en, en_US, nds, C

    There may be an underscore or no underscore. The first part may
    contain two or three letters.

    There will not be a dot like `en_US.UTF-8`.
    """
    try:
        from bleachbit.Options import options
    except ImportError:
        logger.error("Failed to get language options")
    else:
        if not options.get('auto_detect_lang'):
            # PurgeBit: when auto-detection is disabled, use the language the
            # user chose, or the distribution default when none was chosen.
            forced_language = options.get('forced_language') if options.has_option(
                'forced_language') else None
            return normalize_locale_code(forced_language or DEFAULT_LANGUAGE)
    # locale.getdefaultlocale() will be removed in Python 3.15, so
    # use getlocale() instead.
    # However, on Windows, getlocale() may return values like
    # 'English_United States' instead of RFC1766 codes.
    if IS_WINDOWS:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        lcid = kernel32.GetUserDefaultLCID()
        # Convert Windows LCID (e.g., 1033) to RFC1766 (e.g., en-US).
        user_locale = normalize_locale_code(
            locale.windows_locale.get(lcid, ''))
    else:
        # On macOS, locale.getlocale() always returns *something* (e.g.
        # a built-in default) even with no LANG/LC_ALL in the
        # environment at all, so its truthiness cannot detect "nothing
        # was explicitly set". Check os.environ directly instead: if the
        # caller (a shell, a test suite) put LANG/LC_ALL/LC_MESSAGES
        # there on purpose, honor it via locale.getlocale(); otherwise
        # (Finder launches the app with none of these set) prefer the
        # real system preference from AppleLocale.
        env_locale_set = any(
            os.environ.get(name) for name in ("LC_ALL", "LC_MESSAGES", "LANG"))
        if IS_MAC and not env_locale_set:
            from bleachbit.Mac import get_macos_locale
            user_locale = get_macos_locale()
        elif env_locale_set:
            # Read the environment variables instead of
            # locale.getlocale(), which reports the locale set by
            # locale.setlocale() in setup_translation().  Without this,
            # after manually forcing a language once, the detected
            # language would stay stuck on the forced language even
            # after re-enabling auto-detection.
            # LANGUAGE is deliberately not read: setup_translation()
            # sets it on POSIX, so it has the same problem.
            user_locale = None
            for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
                env_value = os.environ.get(name)
                if env_value:
                    candidate = normalize_locale_code(env_value)
                    if len(candidate) >= 2 or candidate == 'C':
                        user_locale = candidate
                        break
        else:
            # Not locale.getlocale(): setlocale() has already poisoned it.
            user_locale = _get_locale_fallback()

    if not user_locale:
        user_locale = 'C'
        logger.warning("no default locale found.  Assuming '%s'", user_locale)

    loc = LocaleCode(user_locale)
    if loc.encoding or loc.modifier:
        # This should never happen.
        logger.warning(
            'locale contains a codeset or modifier: %s', user_locale)
        user_locale = loc.normalized

    assert isinstance(user_locale, str)
    assert len(
        user_locale) >= 2 or user_locale == 'C', f"user_locale: {user_locale}"

    return user_locale


# Bumped by every setup_translation(), so callers can tell it ran again
translation_generation = 0


def setup_translation():
    """Do a one-time setup of translations"""
    # Translation setup runs once per process.
    # pylint: disable-next=global-statement
    global attempted_setup_translation, t, translation_generation
    attempted_setup_translation = True
    translation_generation += 1
    # Use local import to avoid circular import.
    from bleachbit import locale_dir
    # On POSIX, capture the system locale before setlocale() below poisons
    # getlocale(). This must precede get_active_language_code(), which
    # returns early on a forced language without capturing it.
    if IS_POSIX:
        _get_locale_fallback()
    user_locale = get_active_language_code()
    logger.debug("user_locale: %s, locale_dir: %s", user_locale, locale_dir)
    assert isinstance(user_locale, str)
    assert isinstance(locale_dir, str), f"locale_dir: {locale_dir}"
    if IS_WINDOWS and user_locale:
        os.environ['LANG'] = user_locale
    elif IS_POSIX and user_locale:
        # GLib's own g_get_language_names() (used by Gtk.Builder to
        # translate .ui files like the hamburger menu) reads LANGUAGE
        # directly from the environment with top priority -- it does
        # not consult locale.setlocale()'s C-level locale state at
        # all, so without this, a Gtk.Builder-loaded menu stays frozen
        # in whatever locale the OS environment happened to have at
        # process launch (e.g. always the real macOS AppleLocale
        # preference), never following a later in-app language change.
        # Confirmed by hand: without setting this, the app-menu.ui
        # hamburger menu stayed in Spanish through three different
        # manually-selected languages and a full app restart, on a
        # machine whose real System Settings > Language is Spanish;
        # setting it here made it follow the selected language
        # immediately.
        #
        # Only LANGUAGE is set, deliberately not LANG/LC_ALL: LANGUAGE
        # is a GNU gettext-specific extension used purely as a
        # translation-priority hint and has no effect on the process's
        # actual C locale/encoding, whereas LANG/LC_ALL are read by
        # every other locale-aware consumer in the process, including
        # any subprocess spawned afterward -- and a bare code without
        # an explicit encoding (which GLib's own parsing of these
        # variables specifically requires; a '.UTF-8' suffix breaks it,
        # verified by hand) crashed a subprocess Python interpreter
        # started later with 'Fatal Python error:
        # config_get_locale_encoding: ... nl_langinfo(CODESET) failed'
        # when this was tried with LANG/LC_ALL instead.
        os.environ['LANGUAGE'] = user_locale
    text_domain = 'bleachbit'
    try:
        t = gettext.translation(
            domain=text_domain, localedir=locale_dir, languages=[user_locale], fallback=True)
    except FileNotFoundError as e:
        logger.error(
            "Error in setup_translation() with language code %s: %s", user_locale, e)
        t = None
        return
    if hasattr(locale, 'bindtextdomain'):
        locale.bindtextdomain(text_domain, locale_dir)
        locale.textdomain(text_domain)
    elif IS_WINDOWS:
        from bleachbit.Windows import flush_gettext_cache, load_i18n_dll
        libintl = load_i18n_dll()
        if not libintl:
            logger.error(
                'The internationalization library is not available.')
            return
        assert isinstance(text_domain, str)
        encoded_domain = text_domain.encode('utf-8')
        # wbindtextdomain(char, wchar): first parameter is encoded
        if hasattr(libintl, 'libintl_wbindtextdomain'):
            libintl.libintl_wbindtextdomain(encoded_domain, locale_dir)
            libintl.textdomain(encoded_domain)
            libintl.bind_textdomain_codeset(encoded_domain, b'UTF-8')
            # Without this flush, Gtk.Builder / g_dgettext keep serving the
            # .mo loaded for the previous language for the rest of the
            # process (issue #1801). Env-var changes and re-binding the
            # domain are not enough on the Windows gettext build.
            if not flush_gettext_cache(libintl):
                logger.warning(
                    'Failed to flush gettext cache; the GUI may stay in the '
                    'previous language.')
        else:
            logger.error(
                'The function wbindtextdomain() is not available.')

        # Log for debugging
        logger.debug("Windows translation domain set to: %s, dir: %s",
                     text_domain, locale_dir)
    else:
        logger.error('The function bindtextdomain() is not available.')

    # locale.setlocale() on Linux will throw an exception if the locale is not
    # available, so find the best matching locale. When set, Gtk.Builder is
    # translated.
    # On Windows, locale.setlocale() accepts any values without raising an exception.
    if IS_POSIX:
        from bleachbit.Unix import find_best_locale
        setlocale_local = find_best_locale(user_locale)
        if 'C' == setlocale_local and not user_locale == 'C':
            logger.warning(
                'locale %s is not available. You may wish to run sudo locale-gen to generate it, or set LC_ALL=C.', user_locale)
        try:
            locale.setlocale(locale.LC_ALL, setlocale_local)
        except locale.Error as e:
            logger.error('locale.setlocale(%s): %s:', setlocale_local, e)


def get_text(text):
    """Return translated string

    The name has an underscore to avoid conflicting with gettext module.
    """
    if not attempted_setup_translation:
        setup_translation()
    if not t:
        return text
    return t.gettext(text)


def get_app_name():
    """Return the application name as shown to the user

    The name is translated, so the English interface shows "PurgeBit" and
    the Chinese interface shows "涤尘".

    The literal below is deliberately written here instead of using
    bleachbit.APP_NAME, because xgettext must see the literal to extract it.
    See the --keyword=get_text option in po/Makefile.
    """
    return get_text('PurgeBit')


def get_app_full_name():
    """Return the application name with both Chinese and English names

    Used for the window title bar, which always shows both names
    regardless of the interface language.
    """
    return '涤尘 PurgeBit'


def nget_text(singular, plural, n):
    """Return translated string with plural variant"""
    if not t:
        if 1 == n:
            return singular
        return plural
    return t.ngettext(singular, plural, n)


def pget_text(msgctxt, msgid):
    """Return translated string with context

    Example context is button
    """
    if not t:
        return msgid
    return t.pgettext(msgctxt, msgid)


attempted_setup_translation = False
t = None
