const maps = new WeakMap();
let library;

function loadLeaflet() {
    if (!library) {
        library = new Promise((resolve, reject) => {
            if (window.L?.map) {
                resolve(window.L);
                return;
            }
            const script = document.createElement("script");
            script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
            script.integrity = "sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=";
            script.crossOrigin = "anonymous";
            script.onload = () => resolve(window.L);
            script.onerror = reject;
            document.head.appendChild(script);
        }).catch((error) => {
            library = undefined;
            throw error;
        });
    }
    return library;
}

function stopPopup(name, detail) {
    const content = document.createElement("div");
    content.className = "route-popup";
    const title = document.createElement("strong");
    title.textContent = `${detail.order} · ${name}`;
    const time = document.createElement("div");
    time.textContent = `${detail.slot} · ${detail.status}`;
    content.append(title, time);

    if (detail.next) {
        const next = document.createElement("div");
        next.className = "route-popup-next";
        const prefix = detail.nextBlocked ? "Conflict preview" : "Walk next to";
        const distance = detail.meters === undefined ? "" : ` (${detail.meters} m · ~${detail.walkMinutes} min)`;
        next.textContent = `${prefix} ${detail.next}${distance}`;
        content.append(next);
    }
    return content;
}

export default function render({ data, parentElement }) {
    const canvas = parentElement.querySelector(".route-canvas");
    const reset = parentElement.querySelector(".route-reset");
    const status = parentElement.querySelector(".route-status");
    const parkMap = Boolean(data.stopDetails);
    canvas.classList.toggle("route-canvas-park", parkMap);
    canvas.classList.toggle("route-canvas-compact", Boolean(data.compact));
    let view = maps.get(canvas);
    if (!view) {
        view = { revision: 0, follow: false };
        maps.set(canvas, view);
    }
    const revision = ++view.revision;
    loadLeaflet().then((L) => {
        if (revision !== view.revision || !canvas.isConnected) return;
        const firstDraw = !view.map;
        if (firstDraw) {
            view.map = L.map(canvas, { scrollWheelZoom: false, preferCanvas: false,
                zoomSnap: parkMap ? 0.25 : 1 });
            L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                maxZoom: 19,
            }).addTo(view.map);
            view.layer = L.layerGroup().addTo(view.map);
        }

        const legs = data.legs ?? (data.path?.length ? [{ path: data.path }] : []);
        const signature = JSON.stringify([data.points, legs]);
        const changedRoute = signature !== view.signature;
        const fitRoute = () => {
            view.map.stop();
            view.map.invalidateSize({ pan: false });
            const bounds = L.latLngBounds(data.points);
            for (const leg of legs) bounds.extend(L.latLngBounds(leg.path));
            const padding = parkMap ? [30, 30] : [44, 44];
            view.map.fitBounds(bounds, { padding, maxZoom: data.maxFitZoom ?? 13, animate: false });
        };

        view.layer.clearLayers();
        for (const leg of legs) {
            L.polyline(leg.path, {
                className: leg.blocked ? "routed-line route-conflict" : "routed-line",
                color: leg.blocked ? "#99451b" : "#126E62",
                dashArray: leg.blocked ? "7 7" : null,
                weight: 4, opacity: 0.9, smoothFactor: 0.3,
            }).addTo(view.layer);
        }
        data.points.forEach((point, i) => {
            const detail = data.stopDetails?.[i];
            const name = data.names[i] || `Stop ${i + 1}`;
            const order = detail?.order ?? i + 1;
            const classes = ["route-marker"];
            if (detail?.blocked) classes.push("route-marker-blocked");
            else if (detail?.order === 1) classes.push("route-marker-start");
            else if (detail ? detail.status !== "Original plan" : i > 0 && i < data.points.length - 1) {
                classes.push("route-stop");
            }
            const icon = L.divIcon({
                className: classes.join(" "),
                html: String(order),
                iconSize: [26, 26],
                iconAnchor: [13, 13],
            });
            const label = document.createElement("span");
            label.textContent = detail ? `${order} · ${name}` : name;
            const marker = L.marker(point, { icon, title: label.textContent, alt: label.textContent })
                .bindTooltip(label);
            if (detail) {
                marker.bindPopup(stopPopup(name, detail));
                marker.on("add", () => marker.getElement().setAttribute("aria-label", label.textContent));
            }
            marker.addTo(view.layer);
        });
        if (firstDraw || (data.follow && (changedRoute || !view.follow))) fitRoute();
        view.signature = signature;
        view.follow = data.follow;

        reset.disabled = false;
        reset.onclick = () => {
            fitRoute();
            status.hidden = false;
            status.textContent = "Route centered";
            clearTimeout(view.statusTimer);
            view.statusTimer = setTimeout(() => { status.hidden = true; }, 1600);
        };
        status.hidden = true;
    }).catch(() => {
        if (revision !== view.revision || !canvas.isConnected) return;
        status.textContent = data.fallback || (parkMap ? "Map unavailable. See the park order below."
            : "Map unavailable. Open the route in Google Maps below.");
        status.hidden = false;
    });
}
