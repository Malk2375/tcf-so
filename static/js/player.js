/*
 * Lecteur d'examen (SPEC 8.5). Composant Alpine.js, sans étape de compilation.
 *
 * Le serveur fait foi : les minuteurs se calculent à partir des échéances qu'il envoie
 * et du décalage avec son horloge, jamais à partir d'un compteur local.
 * La bonne réponse ne parvient jamais au navigateur pendant le passage.
 */
document.addEventListener("alpine:init", () => {
  const LETTERS = ["A", "B", "C", "D"];
  const RETRY_MS = 5000;
  // Délai de démarrage automatique des consignes de MSL et de CE en mode réel (8.2).
  const AUTO_START_MS = 30000;

  class NetworkError extends Error {}
  class Conflict extends Error {
    constructor(data) {
      super(data.message || "Action refusée");
      this.data = data;
    }
  }

  function csrfToken() {
    // Jeton de la page (le nom du cookie change sur le site de démonstration).
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  Alpine.data("player", (config) => ({
    config,
    phase: "loading", // loading | instructions | co | qcm | sending | error
    message: "",
    sections: [],
    section: null, // résumé de l'épreuve affichée
    offset: 0, // heure du serveur − heure locale, en ms
    now: Date.now(),
    online: true,
    queue: {},
    autoStartAt: null,
    wakeLock: null,
    soundPlaying: false,
    // Compréhension orale
    audioUnlocked: false,
    coItem: null,
    coPhase: "idle", // ready | listening | answering | sending
    coShownAt: 0,
    countdownEnd: 0,
    audioReported: false,
    // MSL et CE
    items: [],
    index: 0,
    enteredAt: 0,
    deadline: 0,
    gridOpen: false,
    confirmOpen: false,
    finishing: false,

    // --- Démarrage --------------------------------------------------------------------------

    init() {
      this.loadQueue();
      setInterval(() => this.tick(), 250);
      setInterval(() => this.flushQueue(), RETRY_MS);
      window.addEventListener("keydown", (event) => this.onKey(event));
      window.addEventListener("pointerdown", () => this.onGesture(), true);
      window.addEventListener("online", () => this.flushQueue());
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && this.inSection) this.requestWakeLock();
      });
      const audio = this.$refs.audio;
      audio.addEventListener("playing", () => this.onAudioPlaying());
      audio.addEventListener("ended", () => this.onAudioEnded());
      audio.addEventListener("error", () => this.onAudioEnded());
      this.sync();
    },

    get inSection() {
      return this.phase === "co" || this.phase === "qcm";
    },

    async sync() {
      try {
        const state = await this.request("GET", this.config.urls.state);
        this.route(state);
      } catch (error) {
        this.retryLater(() => this.sync(), error);
      }
    },

    route(state) {
      if (state.redirect) return this.leave(state.redirect);
      this.sections = state.sections;
      const next = this.sections.find((s) => s.status !== "done");
      if (!next) {
        // Passage en cours de clôture : l'état suivant contiendra la redirection.
        setTimeout(() => this.sync(), 1000);
        return;
      }
      this.section = next;
      if (next.status === "in_progress") return this.start(next, false);
      this.showInstructions(next);
    },

    leave(url) {
      this.releaseWakeLock();
      window.location.assign(url);
    },

    retryLater(action, error) {
      if (!(error instanceof NetworkError)) {
        this.phase = "error";
        this.message = error.message;
        return;
      }
      this.online = false;
      setTimeout(action, 2000);
    },

    // --- Réseau -----------------------------------------------------------------------------

    async request(method, url, body) {
      let response;
      const sentAt = Date.now();
      try {
        response = await fetch(url, {
          method,
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken(),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch (error) {
        throw new NetworkError(error.message);
      }
      if (response.status >= 500) throw new NetworkError(`HTTP ${response.status}`);
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        throw new NetworkError("Réponse illisible");
      }
      this.online = true;
      if (data.server_time) {
        // Heure du serveur au milieu de l'aller-retour.
        const midpoint = (sentAt + Date.now()) / 2;
        this.offset = Date.parse(data.server_time) - midpoint;
      }
      if (response.status === 409) throw new Conflict(data);
      if (response.status === 401 || response.status === 403) {
        this.leave(this.config.urls.dashboard);
        throw new Error(data.message || "Accès refusé");
      }
      if (!response.ok) throw new Error(data.message || `HTTP ${response.status}`);
      return data;
    },

    serverNow() {
      return this.now + this.offset;
    },

    // --- File d'attente hors ligne ----------------------------------------------------------

    queueKey() {
      return `tcf-answers-${this.config.attemptId}`;
    },

    loadQueue() {
      try {
        this.queue = JSON.parse(localStorage.getItem(this.queueKey()) || "{}");
      } catch (error) {
        this.queue = {};
      }
    },

    saveQueue() {
      try {
        localStorage.setItem(this.queueKey(), JSON.stringify(this.queue));
      } catch (error) {
        /* stockage indisponible : la file reste en mémoire */
      }
    },

    get queued() {
      return Object.keys(this.queue).length;
    },

    async sendAnswer(payload) {
      // Un envoi plus récent remplace toute réponse en attente pour la même question.
      delete this.queue[payload.test_item_id];
      this.saveQueue();
      try {
        await this.request("POST", this.config.urls.answers, payload);
      } catch (error) {
        if (error instanceof NetworkError) {
          this.queue[payload.test_item_id] = payload;
          this.saveQueue();
          this.online = false;
        } else if (error instanceof Conflict) {
          this.onConflict(error);
        }
      }
    },

    async flushQueue() {
      for (const key of Object.keys(this.queue)) {
        const payload = this.queue[key];
        try {
          await this.request("POST", this.config.urls.answers, payload);
        } catch (error) {
          if (error instanceof NetworkError) {
            this.online = false;
            return;
          }
          // Réponse refusée (échéance passée, question close) : elle ne sera jamais acceptée.
        }
        if (this.queue[key] === payload) delete this.queue[key];
        this.saveQueue();
      }
    },

    onConflict(error) {
      // L'épreuve ou la question a été close par le serveur : on se recale sur son état.
      if (["section_closed", "not_current", "attempt_closed"].includes(error.data.error)) {
        this.sync();
      }
    },

    // --- Consignes et démarrage d'une épreuve -------------------------------------------------

    showInstructions(section) {
      this.phase = "instructions";
      this.releaseWakeLock();
      this.autoStartAt =
        this.config.mode === "reel" && section.section !== "CO" ? Date.now() + AUTO_START_MS : null;
    },

    get previousSection() {
      if (!this.section) return null;
      return this.sections.find((s) => s.order === this.section.order - 1) || null;
    },

    get autoStartSeconds() {
      return this.autoStartAt ? Math.max(0, Math.ceil((this.autoStartAt - this.now) / 1000)) : null;
    },

    unlockAudio(src) {
      // iOS n'autorise la lecture qu'après un appui : on lance tout de suite une première lecture
      // sur l'unique élément <audio>, dont seul `src` changera ensuite.
      const audio = this.$refs.audio;
      audio.src = src || this.config.silenceUrl;
      const played = audio.play();
      if (played) played.then(() => (this.audioUnlocked = true)).catch(() => {});
      this.audioUnlocked = true;
    },

    testSound() {
      this.soundPlaying = true;
      this.unlockAudio(this.config.soundCheckUrl);
      setTimeout(() => (this.soundPlaying = false), 2000);
    },

    async start(section, fromButton = true) {
      this.autoStartAt = null;
      if (fromButton && section.section === "CO") this.unlockAudio();
      this.phase = "loading";
      try {
        const session = await this.request("POST", this.config.urls.sections[section.order].start);
        this.enterSection(session);
      } catch (error) {
        if (error instanceof Conflict) return this.sync();
        this.retryLater(() => this.start(section, false), error);
      }
    },

    enterSection(session) {
      if (session.redirect) return this.leave(session.redirect);
      if (session.section_done) return this.sync();
      this.section = session.section;
      this.requestWakeLock();
      if (session.section.section === "CO") return this.showCoItem(session.item);
      this.items = session.items;
      this.deadline = Date.parse(session.section.deadline_at);
      const firstOpen = this.items.findIndex((item) => item.selected_index === null);
      this.index = firstOpen === -1 ? 0 : firstOpen;
      this.enteredAt = Date.now();
      this.gridOpen = false;
      this.confirmOpen = false;
      this.finishing = false;
      this.phase = "qcm";
      this.$nextTick(() => this.scrollTop());
    },

    // --- Compréhension orale ----------------------------------------------------------------

    showCoItem(item) {
      this.phase = "co";
      this.coItem = item;
      this.coShownAt = Date.now();
      this.audioReported = false;
      this.preloadNext(item.next_audio_url);
      if (!item.audio_url) return this.startCountdown(item.answer_window_seconds);
      if (!item.can_play_audio) {
        // Audio déjà relancé une fois : seul le décompte reprend.
        return this.startCountdown(item.answer_window_seconds);
      }
      this.playItemAudio();
    },

    // Lecture refusée par le navigateur (rechargement sans appui, iOS) : on attend un appui,
    // mais la question ne reste pas ouverte indéfiniment — elle se clôt à son heure normale.
    waitForGesture() {
      const item = this.coItem;
      this.audioUnlocked = false;
      this.coPhase = "ready";
      const nominal =
        Date.parse(item.first_seen_at) -
        this.offset +
        item.audio_duration_ms +
        item.answer_window_seconds * 1000;
      let end = Math.max(nominal, Date.now() + item.answer_window_seconds * 1000);
      const closesAt = Date.parse(item.closes_at) - this.offset;
      if (!Number.isNaN(closesAt)) end = Math.min(end, closesAt);
      this.countdownEnd = end;
    },

    resumeAudio() {
      if (this.coPhase !== "ready") return;
      this.unlockAudio(this.coItem.audio_url);
      this.coPhase = "listening";
    },

    onGesture() {
      // Tout appui déverrouille l'audio pour les questions suivantes.
      if (this.phase !== "co" || this.audioUnlocked) return;
      if (this.coPhase === "ready") this.resumeAudio();
      else this.unlockAudio();
    },

    playItemAudio() {
      const audio = this.$refs.audio;
      this.coPhase = "listening";
      audio.src = this.coItem.audio_url;
      const played = audio.play();
      if (played) {
        played.then(() => (this.audioUnlocked = true)).catch(() => this.waitForGesture());
      }
    },

    onAudioPlaying() {
      if (this.phase !== "co" || this.coPhase !== "listening" || this.audioReported) return;
      if (!this.coItem || !this.$refs.audio.src.endsWith(this.coItem.audio_url)) return;
      this.audioReported = true;
      this.request("POST", this.config.urls.audioStarted).catch(() => {});
    },

    onAudioEnded() {
      if (this.phase !== "co" || this.coPhase !== "listening") return;
      if (!this.coItem || !this.$refs.audio.src.endsWith(this.coItem.audio_url || "\u0000")) return;
      this.startCountdown(this.coItem.answer_window_seconds);
    },

    preloadNext(url) {
      const preload = this.$refs.preload;
      if (url && !preload.src.endsWith(url)) {
        preload.src = url;
        preload.load();
      }
    },

    startCountdown(seconds) {
      this.coPhase = "answering";
      let end = Date.now() + seconds * 1000;
      // Ne jamais dépasser l'instant où le serveur clora la question d'office.
      const closesAt = Date.parse(this.coItem.closes_at) - this.offset;
      if (!Number.isNaN(closesAt)) end = Math.min(end, closesAt);
      this.countdownEnd = end;
    },

    get countdownSeconds() {
      return Math.max(0, Math.ceil((this.countdownEnd - this.now) / 1000));
    },

    get countdownPercent() {
      if (!this.coItem) return 0;
      const total = this.coItem.answer_window_seconds * 1000;
      return Math.max(0, Math.min(100, ((this.countdownEnd - this.now) / total) * 100));
    },

    async nextCoItem() {
      const closing = this.coItem;
      this.coPhase = "sending";
      try {
        const session = await this.request("POST", this.config.urls.coNext, {
          test_item_id: closing.test_item_id,
        });
        this.enterSection(session);
      } catch (error) {
        if (error instanceof Conflict) return this.sync();
        this.retryLater(() => this.nextCoItem(), error);
      }
    },

    // --- Choix d'une réponse ----------------------------------------------------------------

    get current() {
      return this.phase === "co" ? this.coItem : this.items[this.index];
    },

    canAnswer() {
      if (this.phase === "co") return ["listening", "answering", "ready"].includes(this.coPhase);
      return this.phase === "qcm" && !this.finishing;
    },

    choose(index) {
      const item = this.current;
      if (!item || !this.canAnswer()) return;
      item.selected_index = index;
      this.sendAnswer(this.payload(item));
    },

    toggleFlag() {
      const item = this.current;
      if (!item || this.phase !== "qcm") return;
      item.flagged = !item.flagged;
      this.sendAnswer(this.payload(item));
    },

    payload(item) {
      let spent = item.time_spent_ms || 0;
      if (this.phase === "co") spent = Date.now() - this.coShownAt;
      else spent += Date.now() - this.enteredAt;
      return {
        test_item_id: item.test_item_id,
        selected_index: item.selected_index,
        flagged: !!item.flagged,
        time_spent_ms: Math.round(spent),
      };
    },

    letter(index) {
      return LETTERS[index];
    },

    // --- MSL et CE : navigation -------------------------------------------------------------

    goTo(index) {
      if (index === this.index || index < 0 || index >= this.items.length) return;
      if (index < this.index && !this.section.allow_back) return;
      const leaving = this.items[this.index];
      const payload = this.payload(leaving);
      leaving.time_spent_ms = payload.time_spent_ms;
      this.sendAnswer(payload);
      this.index = index;
      this.enteredAt = Date.now();
      this.gridOpen = false;
      this.$nextTick(() => this.scrollTop());
    },

    scrollTop() {
      window.scrollTo({ top: 0 });
      if (this.$refs.stimulus) this.$refs.stimulus.scrollTop = 0;
    },

    get unanswered() {
      return this.items.filter((item) => item.selected_index === null).length;
    },

    gridClass(item, index) {
      if (index === this.index) return "grid-cell grid-cell-current";
      if (item.flagged) return "grid-cell grid-cell-flagged";
      if (item.selected_index !== null) return "grid-cell grid-cell-answered";
      return "grid-cell";
    },

    async finishSection() {
      if (this.finishing) return;
      this.finishing = true;
      this.confirmOpen = false;
      const item = this.items[this.index];
      if (item) await this.sendAnswer(this.payload(item));
      await this.flushQueue();
      try {
        const session = await this.request(
          "POST",
          this.config.urls.sections[this.section.order].finish,
        );
        this.items = [];
        this.enterSection(session);
      } catch (error) {
        if (error instanceof Conflict) return this.sync();
        this.finishing = false;
        this.retryLater(() => this.finishSection(), error);
      }
    },

    // --- Minuteurs --------------------------------------------------------------------------

    tick() {
      this.now = Date.now();
      if (this.phase === "instructions" && this.autoStartAt && this.now >= this.autoStartAt) {
        this.start(this.section, false);
      }
      const counting = this.coPhase === "answering" || this.coPhase === "ready";
      if (this.phase === "co" && counting && this.now >= this.countdownEnd) {
        this.nextCoItem();
      }
      if (this.phase === "qcm" && !this.finishing && this.serverNow() >= this.deadline) {
        this.finishSection(); // échéance : envoi des réponses en attente puis clôture
      }
    },

    get remainingSeconds() {
      if (!this.section || !this.section.deadline_at) return 0;
      const deadline = this.phase === "qcm" ? this.deadline : Date.parse(this.section.deadline_at);
      return Math.max(0, Math.floor((deadline - this.serverNow()) / 1000));
    },

    get timer() {
      const total = this.remainingSeconds;
      const minutes = String(Math.floor(total / 60)).padStart(2, "0");
      const seconds = String(total % 60).padStart(2, "0");
      return `${minutes}:${seconds}`;
    },

    get progressLabel() {
      if (!this.section) return "";
      const position = this.phase === "co" ? this.coItem?.position : this.index + 1;
      return position ? `Question ${position}/${this.section.n_items}` : "";
    },

    formatDuration(seconds) {
      const minutes = Math.round(seconds / 60);
      return minutes >= 1 ? `${minutes} min` : `${seconds} s`;
    },

    // --- Clavier et veille ------------------------------------------------------------------

    onKey(event) {
      if (event.ctrlKey || event.metaKey || event.altKey || this.confirmOpen) return;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
      const key = event.key.toLowerCase();
      const choice = { a: 0, b: 1, c: 2, d: 3, 1: 0, 2: 1, 3: 2, 4: 3 }[key];
      if (choice !== undefined && this.canAnswer()) {
        event.preventDefault();
        this.choose(choice);
      } else if (this.phase === "qcm" && event.key === "ArrowRight") {
        this.goTo(this.index + 1);
      } else if (this.phase === "qcm" && event.key === "ArrowLeft") {
        this.goTo(this.index - 1);
      }
    },

    async requestWakeLock() {
      try {
        if ("wakeLock" in navigator && !this.wakeLock) {
          this.wakeLock = await navigator.wakeLock.request("screen");
          this.wakeLock.addEventListener("release", () => (this.wakeLock = null));
        }
      } catch (error) {
        /* veille non disponible : sans conséquence */
      }
    },

    releaseWakeLock() {
      if (this.wakeLock) this.wakeLock.release().catch(() => {});
      this.wakeLock = null;
    },

    async abandonAttempt() {
      if (!window.confirm("Abandonner ce passage ? Il ne pourra pas être repris.")) return;
      try {
        await this.request("POST", this.config.urls.abandon);
      } finally {
        this.leave(this.config.urls.dashboard);
      }
    },
  }));
});
