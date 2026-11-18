"""Tests de bout en bout dans Chromium (Playwright) : lecteur aux quatre tailles d'écran,
application installable, service worker (SPEC 8, 14 et 17 ; PLAN étape 6).

Le navigateur ne joint que le serveur de test local. Il faut Chromium pour Playwright :
`uv run playwright install chromium` ; sans lui, ces tests sont ignorés (avec un message).
"""

import os
import random
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import expect

from bank import audio, synthesis
from bank.devtest import make_dev_test
from bank.models import FormatSection
from exam import services
from exam.models import Attempt
from tests.conftest import PASSWORD

pytestmark = pytest.mark.e2e

# L'API synchrone de Playwright fait tourner une boucle asyncio dans le fil des tests ; la base de
# test y reste utilisée de façon synchrone, depuis ce seul fil : Django doit le tolérer.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

VIEWPORTS = {
    "360x640": (360, 640),
    "390x844": (390, 844),
    "768x1024": (768, 1024),
    "1280x800": (1280, 800),
}
SILENCE = (Path(settings.BASE_DIR) / "static" / "audio" / "silence.mp3").read_bytes()


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Les audios de CO s'enchaînent sans nouvel appui, comme sur un appareil déjà déverrouillé.
    return {**browser_type_launch_args, "args": ["--autoplay-policy=no-user-gesture-required"]}


@pytest.fixture(autouse=True)
def _e2e_environment(playwright, monkeypatch):
    if not Path(playwright.chromium.executable_path).exists():
        pytest.skip("Chromium pour Playwright absent : uv run playwright install chromium")

    def fake_encode(pcm: bytes) -> bytes:
        return b"MP3" + pcm[:64]

    monkeypatch.setattr(audio, "encode_mp3", fake_encode)
    monkeypatch.setattr(synthesis, "encode_mp3", fake_encode)


@pytest.fixture
def candidate(transactional_db, formats):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user("cand", "cand@example.com", PASSWORD)


def open_context(browser, live_server, user, size):
    width, height = size
    context = browser.new_context(
        viewport={"width": width, "height": height},
        has_touch=width < 768,
        is_mobile=width < 768,
        locale="fr-FR",
        service_workers="block",  # le lecteur est testé seul ; le service worker a ses tests
    )
    if user is not None:
        client = Client()
        client.force_login(user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        context.add_cookies(
            [{"name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": live_server.url}]
        )
    # Chaque audio de CO est remplacé par un silence très court : le test reste rapide.
    context.route(
        "**/media/**",
        lambda route: route.fulfill(status=200, body=SILENCE, content_type="audio/mpeg"),
    )
    return context


# --- Contrôles de mise en page ------------------------------------------------------------------


def assert_fits(page, where):
    """Pas de défilement horizontal ; boutons visibles d'au moins 44 px de haut."""
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0, f"{where} : défilement horizontal de {overflow} px"
    small = page.evaluate(
        """() => [...document.querySelectorAll('button, a.btn, .option')]
              .filter(el => el.offsetParent !== null)
              .map(el => [el.textContent.trim().slice(0, 30), el.getBoundingClientRect()])
              .filter(([, r]) => r.width > 0 && r.height < 43.5)
              .map(([text, r]) => `${text} (${Math.round(r.height)} px)`)"""
    )
    assert not small, f"{where} : zones tactiles trop petites : {small}"


def player_state(page) -> dict:
    return page.evaluate(
        """() => { const d = Alpine.$data(document.querySelector('[x-data^="player"]'));
                   return {phase: d.phase, coPhase: d.coPhase,
                           section: d.section && d.section.section,
                           index: d.index, count: d.items ? d.items.length : 0}; }"""
    )


def wait_phase(page, phases, timeout=15000):
    page.wait_for_function(
        """(phases) => { const d = Alpine.$data(document.querySelector('[x-data^="player"]'));
                         return phases.includes(d.phase); }""",
        arg=phases,
        timeout=timeout,
    )


def choose_first_option(page):
    page.locator("section:visible button.option").first.click()


# --- Lecteur aux quatre tailles -----------------------------------------------------------------


def run_co(page, label):
    page.get_by_role("button", name="Commencer l'épreuve").click()
    wait_phase(page, ["co"])
    assert_fits(page, f"{label}, CO")
    assert page.locator("audio").count() == 2  # un seul lecteur, plus le préchargement
    assert page.locator("audio[controls]").count() == 0  # ni pause ni barre de déplacement
    answered = 0
    while True:
        state = player_state(page)
        if state["phase"] == "instructions":
            break
        listening = state["phase"] == "co" and state["coPhase"] in {"listening", "answering"}
        selected = page.locator("section:visible button.option[aria-pressed='true']")
        if listening and not selected.count():
            choose_first_option(page)
            answered += 1
        page.wait_for_timeout(150)
    return answered


def run_qcm(page, label, section):
    page.get_by_role("button", name="Commencer l'épreuve").click()
    wait_phase(page, ["qcm"])
    state = player_state(page)
    assert state["section"] == section
    assert_fits(page, f"{label}, {section}")
    width = page.viewport_size["width"]
    if section == "CE":
        stimulus = page.locator("[x-ref='stimulus']").bounding_box()
        options = page.locator("section:visible button.option").first.bounding_box()
        if width > 1024:
            assert options["x"] > stimulus["x"] + stimulus["width"] - 1, "CE : deux colonnes"
        else:
            assert options["y"] > stimulus["y"], "CE : texte puis question"
    nav = page.get_by_role("navigation", name="Navigation entre les questions")
    if width < 1024:
        # Barre Précédent/Suivant fixée en bas ; grille dans un panneau ouvert depuis le bandeau.
        box = nav.bounding_box()
        assert abs(box["y"] + box["height"] - page.viewport_size["height"]) <= 1
        page.get_by_role("button", name="Questions").click()
        expect(page.locator("#grille")).to_be_visible()
        assert_fits(page, f"{label}, {section}, grille")
        page.locator("#grille").get_by_role("button", name="Fermer").click()
        expect(page.locator("#grille")).to_be_hidden()
    keyboard = width >= 1024  # sur ordinateur : touches A à D et flèches (SPEC 8.5)
    for number in range(state["count"]):
        if keyboard:
            page.keyboard.press("b")
            expect(page.locator("section:visible button.option").nth(1)).to_have_attribute(
                "aria-pressed", "true"
            )
        else:
            choose_first_option(page)
        if number < state["count"] - 1:
            if keyboard:
                page.keyboard.press("ArrowRight")
                page.wait_for_function(
                    """(n) => Alpine.$data(
                        document.querySelector('[x-data^="player"]')).index === n""",
                    arg=number + 1,
                )
            else:
                nav.get_by_role("button", name="Suivant").click()
    nav.get_by_role("button", name="Terminer").click()
    page.get_by_role("dialog").get_by_role("button", name="Terminer l'épreuve").click()
    return state["count"]


@pytest.mark.parametrize("size", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_player_end_to_end(browser, live_server, candidate, size):
    test = make_dev_test("rapide", rng=random.Random(4))
    FormatSection.objects.filter(format=test.format, section="CO").update(answer_window_seconds=1)
    attempt = services.create_attempt(candidate, test)
    label = "×".join(map(str, size))
    context = open_context(browser, live_server, candidate, size)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(f"{live_server.url}/attempts/{attempt.pk}/")
    wait_phase(page, ["instructions"])
    assert_fits(page, f"{label}, consignes")

    answered = run_co(page, label)
    answered += run_qcm(page, label, "MSL")
    wait_phase(page, ["instructions"])
    answered += run_qcm(page, label, "CE")

    page.wait_for_url("**/results/", timeout=15000)
    assert_fits(page, f"{label}, résultats")
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.COMPLETED
    assert attempt.answers.exclude(selected_index=None).count() == answered == 24
    assert not errors, errors
    context.close()


# --- Application installable et service worker (SPEC 14) ----------------------------------------


def sw_context(browser, live_server):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{live_server.url}/")
    page.evaluate("() => navigator.serviceWorker.ready")
    page.reload()  # la page est désormais contrôlée par le service worker
    page.wait_for_function("() => navigator.serviceWorker.controller !== null")
    return context, page


def test_app_is_installable(browser, live_server, transactional_db):
    context, page = sw_context(browser, live_server)
    cdp = context.new_cdp_session(page)
    errors = cdp.send("Page.getInstallabilityErrors")["installabilityErrors"]
    assert errors == [], errors
    manifest = cdp.send("Page.getAppManifest")
    assert not manifest["errors"], manifest["errors"]
    context.close()


def fetched_by_service_worker(page, path, method="GET") -> bool:
    with page.expect_response(lambda r: r.url.endswith(path)) as info:
        page.evaluate(
            "([path, method]) => fetch(path, {method, credentials: 'same-origin'}).catch(() => {})",
            [path, method],
        )
    return info.value.from_service_worker


def test_service_worker_leaves_media_api_and_posts_alone(browser, live_server, transactional_db):
    context, page = sw_context(browser, live_server)
    # Témoin : l'habillage, lui, passe par le service worker.
    assert fetched_by_service_worker(page, "/static/css/app.css")
    assert not fetched_by_service_worker(page, "/media/audio/absent.mp3")
    assert not fetched_by_service_worker(page, "/api/attempts/1/state")
    assert not fetched_by_service_worker(page, "/bo/")
    assert not fetched_by_service_worker(page, "/accounts/login/", method="POST")
    context.close()


def test_offline_page_when_network_is_lost(browser, live_server, transactional_db):
    context, page = sw_context(browser, live_server)
    context.set_offline(True)
    page.goto(f"{live_server.url}/dashboard/")
    assert "Connexion requise pour passer un test." in page.content()
    assert_fits(page, "hors ligne")
    context.close()


# --- Revue de tous les écrans : 360, 390, 768, 1280 px ; clavier ; contrastes --------------------

CONTRAST_JS = """() => {
  // Les couleurs de Tailwind 4 sont en oklch() : le canevas les convertit en RGBA.
  const ctx = document.createElement("canvas").getContext("2d", {willReadFrequently: true});
  const parse = (c) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0, 0, 0, 0)";
    ctx.fillStyle = c;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const background = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c[3] > 0.5) return c.slice(0, 3);
    }
    return [255, 255, 255];
  };
  const problems = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const el = walker.currentNode.parentElement;
    if (!el || seen.has(el) || !walker.currentNode.textContent.trim()) continue;
    seen.add(el);
    if (el.closest('[disabled], .sr-only, [aria-hidden="true"]')) continue;
    if (el.offsetParent === null) continue;
    const style = getComputedStyle(el);
    if (parseFloat(style.opacity) < 1) continue;
    const fg = parse(style.color).slice(0, 3);
    const bg = background(el);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && parseInt(style.fontWeight) >= 700);
    if (ratio < (large ? 3 : 4.5)) {
      problems.push(`${el.textContent.trim().slice(0, 40)} : ${ratio.toFixed(2)}`);
    }
  }
  return problems;
}"""


def assert_contrast(page, where):
    problems = page.evaluate(CONTRAST_JS)
    assert not problems, f"{where} : contrastes insuffisants : {problems}"


def assert_keyboard_focus_visible(page, where):
    """La touche Tab atteint un élément, dont le focus se voit (contour ou anneau)."""
    page.locator("body").focus()
    page.keyboard.press("Tab")
    visible = page.evaluate(
        """() => { const el = document.activeElement;
                   if (!el || el === document.body) return false;
                   const s = getComputedStyle(el);
                   return (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0)
                          || s.boxShadow !== 'none'; }"""
    )
    assert visible, f"{where} : focus clavier invisible"


@pytest.fixture
def screens(transactional_db, formats):
    """Un compte administrateur qui a tout fait : passages, conseils, brouillon à relire."""
    from django.contrib.auth import get_user_model

    from advice.services import advice_for
    from bank.devtest import make_dev_draft
    from tests.test_advice import complete

    admin = get_user_model().objects.create_superuser("chef", "chef@example.com", PASSWORD)
    rng = random.Random(6)
    attempts = [complete(admin, make_dev_test("rapide", rng=rng), minutes=i) for i in range(3)]
    advice = advice_for(attempts[-1])
    assert advice is not None
    draft = make_dev_draft("rapide", rng=rng, user=admin)
    item = draft.test_items.first().item
    return {
        "user": admin,
        "pages": {
            "accueil (déconnecté)": None,
            "tableau de bord": "/dashboard/",
            "historique": "/history/",
            "résultats": f"/attempts/{attempts[-1].pk}/results/",
            "correction": f"/attempts/{attempts[-1].pk}/review/",
            "conseils": "/advice/",
            "profil": "/profile/",
            "installer": "/install/",
            "mot de passe": "/accounts/password/change/",
            "bo accueil": "/bo/",
            "bo briefs": "/bo/briefs/",
            "bo import": "/bo/import/",
            "bo rapport d'import": f"/bo/import/{draft.import_batch.pk}/",
            "bo tests": "/bo/tests/",
            "bo relecture": f"/bo/tests/{draft.pk}/",
            "bo modifier": f"/bo/tests/{draft.pk}/items/{item.pk}/edit/",
            "bo voix": "/bo/voices/",
            "bo consommation": "/bo/usage/",
            "bo comptes": "/bo/users/",
        },
    }


@pytest.mark.parametrize("size", VIEWPORTS.values(), ids=VIEWPORTS.keys())
def test_every_screen_fits_and_is_readable(browser, live_server, screens, size):
    label = "×".join(map(str, size))
    anonymous = open_context(browser, live_server, None, size)
    page = anonymous.new_page()
    for path, name in (
        ("/", "accueil"),
        ("/accounts/login/", "connexion"),
        ("/accounts/signup/", "inscription"),
        ("/offline/", "hors ligne"),
    ):
        page.goto(live_server.url + path)
        assert_fits(page, f"{label}, {name}")
        assert_contrast(page, f"{label}, {name}")
    assert_keyboard_focus_visible(page, f"{label}, hors ligne")
    anonymous.close()

    context = open_context(browser, live_server, screens["user"], size)
    page = context.new_page()
    for name, path in screens["pages"].items():
        if path is None:
            continue
        response = page.goto(live_server.url + path)
        assert response.ok, f"{name} : HTTP {response.status}"
        page.wait_for_load_state("networkidle")
        assert_fits(page, f"{label}, {name}")
        assert_contrast(page, f"{label}, {name}")
    assert_keyboard_focus_visible(page, f"{label}, comptes")
    context.close()


def test_layout_checks_detect_problems(browser, live_server, transactional_db):
    """Les contrôles ci-dessus ne sont pas vides : ils repèrent un défaut volontaire."""
    context = open_context(browser, live_server, None, VIEWPORTS["360x640"])
    page = context.new_page()
    page.goto(f"{live_server.url}/offline/")
    page.evaluate(
        """() => document.querySelector('main').insertAdjacentHTML('beforeend',
             '<p style="color:#d4d4d4">Texte pâle</p>'
             + '<button style="min-height:0;height:20px">Petit</button>'
             + '<div style="width:600px">Trop large</div>')"""
    )
    with pytest.raises(AssertionError, match="Texte pâle"):
        assert_contrast(page, "défaut volontaire")
    with pytest.raises(AssertionError, match="défilement horizontal"):
        assert_fits(page, "défaut volontaire")
    page.evaluate("() => document.querySelector('div[style]').remove()")
    with pytest.raises(AssertionError, match="Petit"):
        assert_fits(page, "défaut volontaire")
    context.close()
