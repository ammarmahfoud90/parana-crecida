/* The entrance for every page below the front page: the page title rises
   once, and each block of the content rises as it comes into view. The
   hidden state exists only under the `js` class this script's page sets in
   its head, so without script nothing is ever hidden. Transform and opacity
   only; style.css drops the travel under prefers-reduced-motion. */
(function () {
  "use strict";
  const root = document.documentElement;
  root.classList.add("js");
  requestAnimationFrame(() => requestAnimationFrame(() => root.classList.add("ready")));

  // Static blocks of the page. The map stays still: Leaflet measures its
  // container, and a container moving under it misplaces the first tiles.
  const blocks = [];
  const take = (parent) => {
    let i = 0;
    for (const child of parent.children) {
      if (child.matches("script, [hidden], #map, section")) continue;
      if (child.querySelector("#map")) continue;
      child.classList.add("rise");
      child.style.setProperty("--i", String(Math.min(i, 4)));
      blocks.push(child);
      i += 1;
    }
  };
  const main = document.querySelector("main");
  if (!main) return;
  take(main);
  for (const section of main.querySelectorAll(":scope > section")) take(section);

  if (!("IntersectionObserver" in window)) {
    blocks.forEach((b) => b.classList.add("in"));
    return;
  }
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }
  }, { rootMargin: "0px 0px -6% 0px", threshold: 0.04 });
  blocks.forEach((b) => io.observe(b));
})();
