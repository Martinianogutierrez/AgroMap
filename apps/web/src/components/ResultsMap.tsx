"use client";

import { useEffect, useRef } from "react";

type Zone = {
  id: number;
  name: string;
  areaHa: number;
  yldMean: number | null;
  geom: unknown;
};

export function ResultsMap({
  boundary,
  zones,
}: {
  boundary: unknown;
  zones: Zone[];
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let map: import("maplibre-gl").Map | null = null;
    let cancelled = false;

    async function boot() {
      if (!ref.current) return;
      const maplibregl = await import("maplibre-gl");
      // CSS loaded via layout or global; skip typed css import
      if (cancelled || !ref.current) return;

      map = new maplibregl.Map({
        container: ref.current,
        style: {
          version: 8,
          sources: {
            osm: {
              type: "raster",
              tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
              tileSize: 256,
              attribution: "© OpenStreetMap",
            },
          },
          layers: [{ id: "osm", type: "raster", source: "osm" }],
        },
        center: [-60, -33],
        zoom: 5,
      });

      map.on("load", () => {
        if (!map) return;
        const b = toFeature(boundary);
        if (b?.geometry) {
          map.addSource("boundary", {
            type: "geojson",
            data: b,
          });
          map.addLayer({
            id: "boundary-line",
            type: "line",
            source: "boundary",
            paint: { "line-color": "#1c1917", "line-width": 2 },
          });
        }
        const features: GeoJSON.Feature[] = [];
        for (const z of zones) {
          const g = z.geom as GeoJSON.Geometry | null | undefined;
          if (!g) continue;
          features.push({
            type: "Feature",
            properties: {
              zone_id: z.id,
              zone_name: z.name,
              yld_mean: z.yldMean,
            },
            geometry: g,
          });
        }
        const fc: GeoJSON.FeatureCollection = {
          type: "FeatureCollection",
          features,
        };
        map.addSource("zones", { type: "geojson", data: fc });
        map.addLayer({
          id: "zones-fill",
          type: "fill",
          source: "zones",
          paint: {
            "fill-color": [
              "match",
              ["get", "zone_id"],
              0,
              "#a50026",
              1,
              "#fdae61",
              2,
              "#66bd63",
              3,
              "#1a9850",
              4,
              "#006837",
              "#888888",
            ],
            "fill-opacity": 0.55,
          },
        });
        map.addLayer({
          id: "zones-outline",
          type: "line",
          source: "zones",
          paint: { "line-color": "#fff", "line-width": 1 },
        });

        try {
          const bounds = new maplibregl.LngLatBounds();
          for (const f of fc.features) {
            const g = f.geometry;
            if (!g) continue;
            if (g.type === "Polygon") {
              for (const c of g.coordinates[0]) bounds.extend(c as [number, number]);
            } else if (g.type === "MultiPolygon") {
              for (const poly of g.coordinates) {
                for (const c of poly[0]) bounds.extend(c as [number, number]);
              }
            }
          }
          if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: 40 });
        } catch {
          /* ignore */
        }
      });
    }

    void boot();
    return () => {
      cancelled = true;
      map?.remove();
    };
  }, [boundary, zones]);

  return (
    <div
      ref={ref}
      className="h-80 w-full overflow-hidden rounded-xl border border-stone-200 bg-stone-100"
    />
  );
}

function toFeature(geom: unknown): GeoJSON.Feature | null {
  if (!geom || typeof geom !== "object") return null;
  const g = geom as { type?: string; features?: GeoJSON.Feature[] };
  if (g.type === "Feature") return geom as GeoJSON.Feature;
  if (g.type === "FeatureCollection") {
    return g.features?.[0] ?? null;
  }
  if (g.type) {
    return {
      type: "Feature",
      properties: {},
      geometry: geom as GeoJSON.Geometry,
    };
  }
  return null;
}
