(() => {
  const body = document.body;
  const repo = body.dataset.repo;

  // Centraliza el repositorio: cambia data-repo en index.html y se actualizan
  // todos los enlaces a GitHub, Releases e Issues.
  if (repo) {
    document.querySelectorAll("[data-github], [data-github-path]").forEach((link) => {
      const path = link.dataset.githubPath || "";
      link.href = `https://github.com/${repo}${path}`;
    });
  }

  const header = document.querySelector(".site-header");
  const onScroll = () => {
    if (header) {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    }
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  const toggle = document.querySelector(".nav-toggle");
  const menu = document.getElementById("menu");
  // Las etiquetas accesibles se definen en cada página para respetar su idioma.
  const labelOpen = toggle?.dataset.labelOpen || "Abrir menú";
  const labelClose = toggle?.dataset.labelClose || "Cerrar menú";

  const closeMenu = () => {
    if (!toggle || !menu) return;
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", labelOpen);
    menu.classList.remove("is-open");
  };

  if (toggle && menu) {
    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      toggle.setAttribute("aria-label", open ? labelOpen : labelClose);
      menu.classList.toggle("is-open", !open);
    });

    menu.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", closeMenu);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });

    window.addEventListener("resize", () => {
      if (window.innerWidth > 760) closeMenu();
    });
  }
})();
