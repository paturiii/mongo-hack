'use client';

import {useEffect, useRef} from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {VStack} from '@astryxdesign/core/Layout';
import type {Slot} from '@/lib/api';

const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{id: 'osm', type: 'raster', source: 'osm'}],
};

/** One day's stops on a map, numbered in visit order. */
export function TripMap({slots}: {slots: Slot[]}) {
  const ref = useRef<HTMLElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    // Bundlers rewrite import.meta.url, so serve MapLibre's worker ourselves (copied on postinstall).
    maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');
    mapRef.current = new maplibregl.Map({container: ref.current, style: STYLE, center: [-9.14, 38.72], zoom: 12, attributionControl: {compact: true}});
    mapRef.current.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
    return () => {
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    markers.current.forEach(m => m.remove());
    const pts = slots.filter(s => s.item.geo?.coordinates).map(s => ({s, c: s.item.geo!.coordinates}));
    markers.current = pts.map(({s, c}, i) =>
      new maplibregl.Marker()
        .setLngLat(c)
        .setPopup(new maplibregl.Popup({offset: 24}).setText(`${i + 1}. ${s.time} · ${s.item.name}`))
        .addTo(map),
    );
    if (pts.length) {
      const b = new maplibregl.LngLatBounds();
      pts.forEach(p => b.extend(p.c));
      map.fitBounds(b, {padding: 48, maxZoom: 15, duration: 400});
    }
  }, [slots]);

  return <VStack ref={ref} height={360} width="100%" aria-label="Map of this day's stops" />;
}
