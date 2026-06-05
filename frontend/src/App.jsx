import React, { useState, useEffect, useRef } from 'react';
import { MapContainer, TileLayer, CircleMarker, GeoJSON, useMap, ImageOverlay, LayersControl, LayerGroup } from 'react-leaflet';
import { WiDaySunny, WiRain, WiCloudy, WiMoonNew, WiMoonWaxingCrescent3, WiMoonFirstQuarter, WiMoonWaxingGibbous3, WiMoonFull, WiMoonWaningGibbous3, WiMoonThirdQuarter, WiMoonWaningCrescent3 } from 'react-icons/wi';
import { Moon } from 'lunarphase-js';
import { createClient } from '@supabase/supabase-js';
import 'leaflet/dist/leaflet.css';
import './App.css';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;
const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Meteorological precipitation colormap: val 0-255 = 0-300 mm/h (power-law scale).
// Matches standard radar palettes: navy → blue → cyan → green → yellow → orange → red → magenta.
const RADAR_STOPS = [
  { v:   0, r:   0, g:   0, b:   0, a:   0 }, // transparent  (0 mm/h)
  { v:   5, r:   0, g:  30, b: 160, a: 185 }, // dark navy    (≈ 0.3 mm/h)
  { v:  26, r:  30, g: 110, b: 220, a: 205 }, // blue         (≈ 1 mm/h)
  { v:  40, r:   0, g: 190, b: 240, a: 220 }, // cyan         (≈ 3 mm/h)
  { v:  65, r:   0, g: 215, b: 110, a: 230 }, // green        (≈ 10 mm/h)
  { v:  94, r: 155, g: 230, b:   5, a: 240 }, // yellow-green (≈ 25 mm/h)
  { v: 130, r: 255, g: 215, b:   0, a: 248 }, // yellow       (≈ 65 mm/h)
  { v: 164, r: 255, g:  95, b:   0, a: 253 }, // orange       (≈ 100 mm/h)
  { v: 210, r: 220, g:  15, b:  10, a: 255 }, // red          (≈ 200 mm/h)
  { v: 255, r: 155, g:   0, b:  80, a: 255 }, // dark magenta (≈ 300 mm/h)
];

const ESTILO_POLIGONOS = {
  color: "#34495e",
  weight: 1,
  fillColor: "#34495e",
  fillOpacity: 0.1
};

function radarColor(val) {
  if (val <= 0) return [0, 0, 0, 0];
  const s = RADAR_STOPS;
  for (let i = s.length - 2; i >= 0; i--) {
    if (val >= s[i].v) {
      const t = (val - s[i].v) / (s[i + 1].v - s[i].v);
      return [
        Math.round(s[i].r + t * (s[i + 1].r - s[i].r)),
        Math.round(s[i].g + t * (s[i + 1].g - s[i].g)),
        Math.round(s[i].b + t * (s[i + 1].b - s[i].b)),
        Math.round(s[i].a + t * (s[i + 1].a - s[i].a)),
      ];
    }
  }
  return [0, 0, 0, 0];
}

function pixelsToDataUrl(w, h, px) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(w, h);
  const d = img.data;
  for (let i = 0; i < px.length; i++) {
    const [r, g, b, a] = radarColor(px[i]);
    const idx = i * 4;
    d[idx] = r; d[idx + 1] = g; d[idx + 2] = b; d[idx + 3] = a;
  }
  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL('image/png');
}

function FlyTo({ coords }) {
  const map = useMap();
  useEffect(() => {
    if (coords) map.flyTo(coords, 12);
  }, [coords, map]);
  return null;
}

function App() {
  const hoyISO = new Date().toISOString().slice(0, 10); // 'YYYY-MM-DD'

  const [estaciones, setEstaciones] = useState([]);
  const [poligonos, setPoligonos] = useState(null);
  const [seleccionada, setSeleccionada] = useState(null);
  const [hora, setHora] = useState(0);
  const [cargando, setCargando] = useState(false);
  const [pronosticoExtendido, setPronosticoExtendido] = useState([]);
  const [busqueda, setBusqueda] = useState('');
  const [flyCoords, setFlyCoords] = useState(null);
  const [indiceBuscador, setIndiceBuscador] = useState(-1);

  // ── Radar ────────────────────────────────────────────────────────────────────
  const [radarActivo, setRadarActivo] = useState(false);
  const [radarFrames, setRadarFrames] = useState([]);   // [{id_raster, fecha, dataUrl}]
  const [radarIndice, setRadarIndice] = useState(0);
  const [radarCargando, setRadarCargando] = useState(false);
  const [radarBounds, setRadarBounds] = useState(null);
  const [radarPausado, setRadarPausado] = useState(false);
  const [radarVelocidad, setRadarVelocidad] = useState(700); // ms por frame
  const [radarFecha, setRadarFecha] = useState(null);              // applied value, triggers reload
  const [radarFechaPendiente, setRadarFechaPendiente] = useState(''); // input draft, not yet applied
  const [radarIntervalo, setRadarIntervalo] = useState(10);  // minutes between frames
  const radarTimelineRef = useRef(null);

  // ── Pysteps ──────────────────────────────────────────────────────────────────
  const [pystepsActivo, setPystepsActivo] = useState(false);
  const [pystepsFrames, setPystepsFrames] = useState([]);   // [{fecha, dataUrl}]
  const [pystepsIndice, setPystepsIndice] = useState(0);
  const [pystepsCargando, setPystepsCargando] = useState(false);
  const [pystepsBounds, setPystepsBounds] = useState(null);
  const [pystepsPausado, setPystepsPausado] = useState(false);
  const [pystepsVelocidad, setPystepsVelocidad] = useState(700); // ms por frame
  const [pystepsFecha, setPystepsFecha] = useState(hoyISO);
  const [pystepsFechaPendiente, setPystepsFechaPendiente] = useState(hoyISO);
  const pystepsTimelineRef = useRef(null);

  // ── Acumulacion horaria ───────────────────────────────────────────────────────
  const [acumActivo, setAcumActivo] = useState(false);
  const [acumFrames, setAcumFrames] = useState([]);
  const [acumIndice, setAcumIndice] = useState(0);
  const [acumCargando, setAcumCargando] = useState(false);
  const [acumBounds, setAcumBounds] = useState(null);
  const [acumPausado, setAcumPausado] = useState(false);
  const [acumVelocidad, setAcumVelocidad] = useState(700);
  const [acumFecha, setAcumFecha] = useState(hoyISO);
  const [acumFechaPendiente, setAcumFechaPendiente] = useState(hoyISO);
  const acumTimelineRef = useRef(null);

  // ── COM2602 ──────────────────────────────────────────────────────────────────
  const [com2602Activo, setCom2602Activo] = useState(false);
  const [com2602Frames, setCom2602Frames] = useState([]);
  const [com2602Indice, setCom2602Indice] = useState(0);
  const [com2602Cargando, setCom2602Cargando] = useState(false);
  const [com2602Bounds, setCom2602Bounds] = useState(null);
  const [com2602Pausado, setCom2602Pausado] = useState(false);
  const [com2602Velocidad, setCom2602Velocidad] = useState(700);
  const [com2602Fecha, setCom2602Fecha] = useState(hoyISO);
  const [com2602FechaPendiente, setCom2602FechaPendiente] = useState(hoyISO);
  const com2602TimelineRef = useRef(null);

  // ── Pysteps 10-minutal ───────────────────────────────────────────────────────
  const [pysteps10minActivo, setPysteps10minActivo] = useState(false);
  const [pysteps10minFrames, setPysteps10minFrames] = useState([]);
  const [pysteps10minIndice, setPysteps10minIndice] = useState(0);
  const [pysteps10minCargando, setPysteps10minCargando] = useState(false);
  const [pysteps10minBounds, setPysteps10minBounds] = useState(null);
  const [pysteps10minPausado, setPysteps10minPausado] = useState(false);
  const [pysteps10minVelocidad, setPysteps10minVelocidad] = useState(700);
  const [pysteps10minFecha, setPysteps10minFecha] = useState(hoyISO);
  const [pysteps10minFechaPendiente, setPysteps10minFechaPendiente] = useState(hoyISO);
  const pysteps10minTimelineRef = useRef(null);

  useEffect(() => {
    const cargarPoligonos = async () => {
      const { data, error } = await supabase.rpc('obtener_limites_geojson');
      if (error) {
        console.error("Error cargando polígonos:", error.message);
      } else {
        setPoligonos(data);
      }
    };
    cargarPoligonos();
  }, []);

  const cargarDatos = async (offset) => {
    setCargando(true);
    const { data, error } = await supabase.rpc('obtener_predicciones_geojson', { p_offset: offset });
    if (error) {
      console.error("Error en Supabase:", error.message);
    } else {
      // PostgREST may return either a single object or a 1-element array
      const geojson = Array.isArray(data) ? data[0] : data;
      if (geojson && geojson.features) setEstaciones(geojson.features);
    }
    setCargando(false);
  };

  const cargarPronosticoEstacion = async (estacionId, offset) => {
    const { data, error } = await supabase.rpc('obtener_prediccion_estacion', {
      p_estacion_id: estacionId,
      p_offset: offset
    });
    if (!error && data) {
      setPronosticoExtendido(data);
    } else {
      console.error("Error cargando ventana temporal:", error);
      setPronosticoExtendido([]);
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => { cargarDatos(hora); }, 300);
    return () => clearTimeout(timer);
  }, [hora]);

  // ── Reload forecast data when time slider changes for selected station ──
  useEffect(() => {
    if (seleccionada && seleccionada.estacion_id) {
      cargarPronosticoEstacion(seleccionada.estacion_id, hora);
    }
  }, [hora, seleccionada?.estacion_id]);

  const cargarRadar = async (fechaDesde = null, intervalo = 10) => {
    setRadarCargando(true);
    setRadarIndice(0);
    const step = intervalo / 10;
    const params = { p_limit: Math.min(20 * step, 60) };
    if (fechaDesde) params.p_desde = `${fechaDesde}:00+00:00`;
    const { data: entradas, error } = await supabase.rpc('obtener_radar_reciente', params);
    if (error || !entradas?.length) {
      console.error('Error cargando radar:', error);
      setRadarCargando(false);
      return;
    }
    const first = entradas[0];
    setRadarBounds([[first.ymin, first.xmin], [first.ymax, first.xmax]]);

    // Clear frames only after confirming new data exists
    setRadarFrames([]);

    // Fetch frames in small batches (4 at a time) — parallel within each batch,
    // sequential between batches — balances speed vs. connection pool pressure
    const toLoad = entradas.filter((_, i) => i % step === 0);
    const results = new Array(toLoad.length).fill(null);
    const BATCH = 4;

    for (let b = 0; b < toLoad.length; b += BATCH) {
      await Promise.all(
        toLoad.slice(b, b + BATCH).map(async (e, j) => {
          const i = b + j;
          const { data: frame, error: fe } = await supabase.rpc('obtener_radar_frame', { p_id_raster: e.id_raster });
          if (!fe && frame) {
            results[i] = { id_raster: e.id_raster, fecha: e.fecha, dataUrl: pixelsToDataUrl(frame.w, frame.h, frame.px) };
            const ready = results.filter(Boolean);
            setRadarFrames([...ready]);
            if (ready.length === 1) setRadarCargando(false);
          } else {
            console.error(`Error en frame ${i} (id=${e.id_raster}):`, fe);
          }
        })
      );
    }
    setRadarCargando(false);
  };

  const toggleRadar   = () => setRadarActivo(prev => !prev);
  const togglePysteps = () => setPystepsActivo(prev => !prev);
  const toggleAcum    = () => setAcumActivo(prev => !prev);
  const toggleCom2602 = () => setCom2602Activo(prev => !prev);
  const togglePysteps10min = () => setPysteps10minActivo(prev => !prev);


  const cargarPysteps = async (fechaParam = null) => {
    setPystepsCargando(true);
    setPystepsIndice(0);

    try {
      // 1. Get list of recent pysteps predictions
      const params = { p_limit: 30 };
      if (fechaParam) params.p_fecha = fechaParam; // 'YYYY-MM-DD'
      const { data: entradas, error: errorReciente } = await supabase.rpc('obtener_pysteps_reciente', params);

      if (errorReciente || !entradas?.length) {
        console.error('Error cargando predicciones:', errorReciente);
        setPystepsFrames([]); // Clear frames when no data found
        setPystepsCargando(false);
        return;
      }

      // Reverse to get chronological order (oldest to newest)
      const entradas_reversed = [...entradas].reverse();
      const first = entradas_reversed[0];
      setPystepsBounds([[first.ymin, first.xmin], [first.ymax, first.xmax]]);

      // 2. Load each frame from the predictions
      setPystepsFrames([]);

      const results = new Array(entradas_reversed.length).fill(null);
      const BATCH = 4;

      for (let b = 0; b < entradas_reversed.length; b += BATCH) {
        await Promise.all(
          entradas_reversed.slice(b, b + BATCH).map(async (e, j) => {
            const i = b + j;
            const { data: frame, error: fe } = await supabase.rpc('obtener_pysteps_frame', { p_id_raster: e.id_raster });
            if (!fe && frame && frame[0]) {
              const frameData = frame[0];
              results[i] = {
                id_raster: e.id_raster,
                fecha: e.fecha,
                dataUrl: pixelsToDataUrl(frameData.w, frameData.h, frameData.px)
              };
              const ready = results.filter(Boolean);
              setPystepsFrames([...ready]);
              if (ready.length === 1) setPystepsCargando(false);
            } else {
              console.error(`Error en frame ${i} (id=${e.id_raster}):`, fe);
            }
          })
        );
      }
      setPystepsCargando(false);
    } catch (err) {
      console.error('Error en cargarPysteps:', err);
      setPystepsCargando(false);
    }
  };

  const cargarAcum = async (fechaParam = null) => {
    setAcumCargando(true);
    setAcumIndice(0);
    try {
      const params = { p_limit: 24 };
      if (fechaParam) params.p_fecha = fechaParam; // 'YYYY-MM-DD'
      const { data: raw, error } = await supabase.rpc('obtener_acumulacion_reciente', params);
      if (error || !raw?.length) {
        console.error('Error cargando acumulacion:', error);
        setAcumCargando(false);
        return;
      }
      // RPC returns DESC (newest first); reverse to get chronological order for timeline
      const entradas = [...raw].reverse();
      const first = entradas[0];
      setAcumBounds([[first.ymin, first.xmin], [first.ymax, first.xmax]]);
      setAcumFrames([]);
      const results = new Array(entradas.length).fill(null);
      const BATCH = 4;
      for (let b = 0; b < entradas.length; b += BATCH) {
        await Promise.all(
          entradas.slice(b, b + BATCH).map(async (e, j) => {
            const i = b + j;
            const { data: frame, error: fe } = await supabase.rpc('obtener_acumulacion_frame', { p_id_raster: e.id_raster });
            if (!fe && frame && frame[0]) {
              const fd = frame[0];
              results[i] = { id_raster: e.id_raster, fecha: e.fecha, dataUrl: pixelsToDataUrl(fd.w, fd.h, fd.px) };
              const ready = results.filter(Boolean);
              setAcumFrames([...ready]);
              if (ready.length === 1) setAcumCargando(false);
            } else {
              console.error(`Error en frame acum ${i}:`, fe);
            }
          })
        );
      }
      setAcumCargando(false);
    } catch (err) {
      console.error('Error en cargarAcum:', err);
      setAcumCargando(false);
    }
  };

  const cargarCom2602 = async (fechaParam = null) => {
    setCom2602Cargando(true);
    setCom2602Indice(0);
    try {
      const params = { p_limit: 30 };
      if (fechaParam) params.p_fecha = fechaParam; // 'YYYY-MM-DD'
      const { data: raw, error } = await supabase.rpc('obtener_com2602_reciente', params);
      if (error || !raw?.length) {
        console.error('Error cargando COM2602:', error);
        setCom2602Frames([]);
        setCom2602Cargando(false);
        return;
      }
      // RPC returns DESC (newest first); reverse to get chronological order for timeline
      const entradas = [...raw].reverse();
      const first = entradas[0];
      setCom2602Bounds([[first.ymin, first.xmin], [first.ymax, first.xmax]]);
      setCom2602Frames([]);
      const results = new Array(entradas.length).fill(null);
      const BATCH = 4;
      for (let b = 0; b < entradas.length; b += BATCH) {
        await Promise.all(
          entradas.slice(b, b + BATCH).map(async (e, j) => {
            const i = b + j;
            const { data: frame, error: fe } = await supabase.rpc('obtener_com2602_frame', { p_id_raster: e.id_raster });
            if (!fe && frame && frame[0]) {
              const fd = frame[0];
              results[i] = { id_raster: e.id_raster, fecha: e.fecha, dataUrl: pixelsToDataUrl(fd.w, fd.h, fd.px) };
              const ready = results.filter(Boolean);
              setCom2602Frames([...ready]);
              if (ready.length === 1) setCom2602Cargando(false);
            } else {
              console.error(`Error en frame COM2602 ${i}:`, fe);
            }
          })
        );
      }
      setCom2602Cargando(false);
    } catch (err) {
      console.error('Error en cargarCom2602:', err);
      setCom2602Cargando(false);
    }
  };

  const cargarPysteps10min = async (fechaParam = null) => {
    setPysteps10minCargando(true);
    setPysteps10minIndice(0);
    try {
      const params = { p_limit: 36 };
      if (fechaParam) params.p_fecha = fechaParam; // 'YYYY-MM-DD'
      const { data: entradas, error: errorReciente } = await supabase.rpc('obtener_pysteps_10min_reciente', params);
      if (errorReciente || !entradas?.length) {
        console.error('Error cargando predicciones 10-minutales:', errorReciente);
        setPysteps10minFrames([]);
        setPysteps10minCargando(false);
        return;
      }
      // Reverse to get chronological order (oldest to newest)
      const entradas_reversed = [...entradas].reverse();
      const first = entradas_reversed[0];
      setPysteps10minBounds([[first.ymin, first.xmin], [first.ymax, first.xmax]]);
      setPysteps10minFrames([]);
      const results = new Array(entradas_reversed.length).fill(null);
      const BATCH = 4;
      for (let b = 0; b < entradas_reversed.length; b += BATCH) {
        await Promise.all(
          entradas_reversed.slice(b, b + BATCH).map(async (e, j) => {
            const i = b + j;
            const { data: frame, error: fe } = await supabase.rpc('obtener_pysteps_10min_frame', { p_id_raster: e.id_raster });
            if (!fe && frame && frame[0]) {
              const frameData = frame[0];
              results[i] = {
                id_raster: e.id_raster,
                fecha: e.fecha,
                dataUrl: pixelsToDataUrl(frameData.w, frameData.h, frameData.px)
              };
              const ready = results.filter(Boolean);
              setPysteps10minFrames([...ready]);
              if (ready.length === 1) setPysteps10minCargando(false);
            } else {
              console.error(`Error en frame pysteps10min ${i} (id=${e.id_raster}):`, fe);
            }
          })
        );
      }
      setPysteps10minCargando(false);
    } catch (err) {
      console.error('Error en cargarPysteps10min:', err);
      setPysteps10minCargando(false);
    }
  };

  // Load (or reload) frames whenever radar turns on, date or interval changes
  useEffect(() => {
    if (radarActivo) cargarRadar(radarFecha, radarIntervalo);
  }, [radarActivo, radarFecha, radarIntervalo]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load pysteps predictions when activated or date changes
  useEffect(() => {
    if (pystepsActivo) cargarPysteps(pystepsFecha);
  }, [pystepsActivo, pystepsFecha]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (acumActivo) cargarAcum(acumFecha);
  }, [acumActivo, acumFecha]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (com2602Activo) cargarCom2602(com2602Fecha);
  }, [com2602Activo, com2602Fecha]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (pysteps10minActivo) cargarPysteps10min(pysteps10minFecha);
  }, [pysteps10minActivo, pysteps10minFecha]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!radarActivo || radarFrames.length === 0 || radarPausado) return;
    const id = setInterval(() => setRadarIndice(i => (i + 1) % radarFrames.length), radarVelocidad);
    return () => clearInterval(id);
  }, [radarActivo, radarFrames, radarPausado, radarVelocidad]);

  useEffect(() => {
    if (!pystepsActivo || pystepsFrames.length === 0 || pystepsPausado) return;
    const id = setInterval(() => setPystepsIndice(i => (i + 1) % pystepsFrames.length), pystepsVelocidad);
    return () => clearInterval(id);
  }, [pystepsActivo, pystepsFrames, pystepsPausado, pystepsVelocidad]);

  useEffect(() => {
    if (!acumActivo || acumFrames.length === 0 || acumPausado) return;
    const id = setInterval(() => setAcumIndice(i => (i + 1) % acumFrames.length), acumVelocidad);
    return () => clearInterval(id);
  }, [acumActivo, acumFrames, acumPausado, acumVelocidad]);

  useEffect(() => {
    if (!com2602Activo || com2602Frames.length === 0 || com2602Pausado) return;
    const id = setInterval(() => setCom2602Indice(i => (i + 1) % com2602Frames.length), com2602Velocidad);
    return () => clearInterval(id);
  }, [com2602Activo, com2602Frames, com2602Pausado, com2602Velocidad]);

  useEffect(() => {
    if (!pysteps10minActivo || pysteps10minFrames.length === 0 || pysteps10minPausado) return;
    const id = setInterval(() => setPysteps10minIndice(i => (i + 1) % pysteps10minFrames.length), pysteps10minVelocidad);
    return () => clearInterval(id);
  }, [pysteps10minActivo, pysteps10minFrames, pysteps10minPausado, pysteps10minVelocidad]);

  // Auto-scroll timeline to keep active segment centred
  useEffect(() => {
    if (!radarTimelineRef.current) return;
    const activo = radarTimelineRef.current.querySelector('.radar-seg.activo');
    if (activo) activo.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [radarIndice]);

  useEffect(() => {
    if (!pystepsTimelineRef.current) return;
    const activo = pystepsTimelineRef.current.querySelector('.pysteps-seg.activo');
    if (activo) activo.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [pystepsIndice]);

  useEffect(() => {
    if (!acumTimelineRef.current) return;
    const activo = acumTimelineRef.current.querySelector('.acum-seg.activo');
    if (activo) activo.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [acumIndice]);

  useEffect(() => {
    if (!com2602TimelineRef.current) return;
    const activo = com2602TimelineRef.current.querySelector('.com2602-seg.activo');
    if (activo) activo.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [com2602Indice]);

  useEffect(() => {
    if (!pysteps10minTimelineRef.current) return;
    const activo = pysteps10minTimelineRef.current.querySelector('.pysteps10min-seg.activo');
    if (activo) activo.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [pysteps10minIndice]);

  const MOON_ICONS = {
    'New':             <WiMoonNew size={22} />,             // Luna nueva
    'Waxing Crescent': <WiMoonWaxingCrescent3 size={22} />, // Creciente
    'First Quarter':   <WiMoonFirstQuarter size={22} />,    // Cuarto creciente
    'Waxing Gibbous':  <WiMoonWaxingGibbous3 size={22} />,  // Gibosa creciente
    'Full':            <WiMoonFull size={22} />,             // Luna llena
    'Waning Gibbous':  <WiMoonWaningGibbous3 size={22} />,  // Gibosa menguante
    'Last Quarter':    <WiMoonThirdQuarter size={22} />,    // Cuarto menguante
    'Waning Crescent': <WiMoonWaningCrescent3 size={22} />, // Menguante
  };

  const getIconoClima = (hora_validez, temperatura, precipitacion) => {
    const fecha = new Date(hora_validez);
    const h = fecha.getUTCHours();
    if (h < 8 || h >= 18) return MOON_ICONS[Moon.lunarPhase(fecha)] ?? <WiMoonNew size={22} />;
    if (precipitacion >= 2) return <WiRain size={22} />;
    if (precipitacion > 0 && temperatura >= 1 && temperatura <= 6) return <WiCloudy size={22} />;
    return <WiDaySunny size={22} />;
  };

  const getColor = (t) => {
    if (t > 30) return '#d73027';
    if (t > 20) return '#fdae61';
    if (t > 10) return '#abd9e9';
    return '#4575b4';
  };

  const resultadosBusqueda = busqueda
    ? estaciones.filter((est) => est.properties.nombre.toLowerCase().includes(busqueda.toLowerCase())).slice(0, 8)
    : [];

  const seleccionarEstacion = (est) => {
    const [lon, lat] = est.geometry.coordinates;
    setFlyCoords([lat, lon]);
    setSeleccionada(est.properties);
    cargarPronosticoEstacion(est.properties.estacion_id, hora);
    setBusqueda('');
    setIndiceBuscador(-1);
  };

  const manejarTeclasBuscador = (e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setIndiceBuscador((prev) => Math.min(prev + 1, resultadosBusqueda.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setIndiceBuscador((prev) => Math.max(prev - 1, -1));
    } else if (e.key === 'Enter' && indiceBuscador >= 0 && resultadosBusqueda[indiceBuscador]) {
      e.preventDefault();
      seleccionarEstacion(resultadosBusqueda[indiceBuscador]);
    } else if (e.key === 'Escape') {
      setBusqueda('');
      setIndiceBuscador(-1);
    }
  };

  const FilaPronostico = ({ label, campo, unidad, className }) => (
    <tr>
      <td><strong>{label}</strong></td>
      {pronosticoExtendido.map((dato, i) => (
        <td key={i} className={className}>
          {dato[campo] != null ? `${dato[campo].toFixed(1)}${unidad}` : '--'}
        </td>
      ))}
    </tr>
  );

  return (
    <div className={`app-container${seleccionada ? ' panel-abierto' : ''}`}>
      {cargando && <div className="loading-overlay">Actualizando mapa...</div>}

      <div className="buscador">
        <input
          type="text"
          placeholder="Buscar estación..."
          value={busqueda}
          onChange={(e) => { setBusqueda(e.target.value); setIndiceBuscador(-1); }}
          onKeyDown={manejarTeclasBuscador}
        />
        {busqueda && (
          <ul className="buscador-resultados">
            {resultadosBusqueda.map((est, i) => (
              <li
                key={i}
                className={i === indiceBuscador ? 'buscador-activo' : ''}
                onClick={() => seleccionarEstacion(est)}
              >
                {est.properties.nombre}
                <span className="buscador-temp">{est.properties.temperatura_2m?.toFixed(1)}°C</span>
              </li>
            ))}
            {resultadosBusqueda.length === 0 && <li className="sin-resultados">Sin resultados</li>}
          </ul>
        )}
      </div>

      <MapContainer center={[40.0, -3.5]} zoom={5} id="map">
        <TileLayer url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png" />
        <FlyTo coords={flyCoords} />

        {poligonos && (
          <GeoJSON
            data={poligonos}
            style={ESTILO_POLIGONOS}
            onEachFeature={(feature, layer) => {
              if (feature.properties && feature.properties.nombre) {
                layer.bindTooltip(feature.properties.nombre);
              }
            }}
          />
        )}

        {radarActivo && radarBounds && radarFrames[radarIndice] && (
          <ImageOverlay
            url={radarFrames[radarIndice].dataUrl}
            bounds={radarBounds}
            opacity={0.85}
          />
        )}

        {pystepsActivo && pystepsBounds && pystepsFrames[pystepsIndice] && (
          <ImageOverlay
            url={pystepsFrames[pystepsIndice].dataUrl}
            bounds={pystepsBounds}
            opacity={0.75}
          />
        )}

        {pysteps10minActivo && pysteps10minBounds && pysteps10minFrames[pysteps10minIndice] && (
          <ImageOverlay
            url={pysteps10minFrames[pysteps10minIndice].dataUrl}
            bounds={pysteps10minBounds}
            opacity={0.75}
          />
        )}

        {acumActivo && acumBounds && acumFrames[acumIndice] && (
          <ImageOverlay
            url={acumFrames[acumIndice].dataUrl}
            bounds={acumBounds}
            opacity={0.80}
          />
        )}

        {com2602Activo && com2602Bounds && com2602Frames[com2602Indice] && (
          <ImageOverlay
            url={com2602Frames[com2602Indice].dataUrl}
            bounds={com2602Bounds}
            opacity={0.75}
          />
        )}

        <LayersControl position="bottomright">
          <LayersControl.Overlay checked name="Estaciones">
            <LayerGroup>
              {estaciones.map((est, idx) => {
                const [lon, lat] = est.geometry.coordinates;
                const { temperatura_2m } = est.properties;
                return (
                  <CircleMarker
                    key={`marker-${idx}`}
                    center={[lat, lon]}
                    radius={7}
                    fillColor={getColor(temperatura_2m)}
                    color="white"
                    weight={1}
                    fillOpacity={0.8}
                    pane="markerPane"
                    eventHandlers={{
                      click: () => {
                        setSeleccionada(est.properties);
                        cargarPronosticoEstacion(est.properties.estacion_id, hora);
                      }
                    }}
                  />
                );
              })}
            </LayerGroup>
          </LayersControl.Overlay>
        </LayersControl>
      </MapContainer>

      {seleccionada && (
        <div className="side-panel">
          <div className="panel-handle" />
          <div className="panel-header">
            <div>
              <h2>{seleccionada.nombre}</h2>
              <p className="subtext">España / Predicción Local</p>
              <p className="forecast-period">
                Mostrando: +{hora}h ({new Date(Date.now() + hora * 3600000).toLocaleString('es-ES', {weekday: 'short', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'})})
              </p>
            </div>
            <button className="close-btn" onClick={() => setSeleccionada(null)}>✕</button>
          </div>

          <div className="resumen-actual">
            <div className="temp-principal">
              <span className="icono-clima">☁️</span>
              <span className="valor">{seleccionada.temperatura_2m?.toFixed(1)}°C</span>
            </div>
            <div className="detalles-grid">
              <div className="detalle-item"><span>Humedad</span> <b>{seleccionada.humedad_rel_pct?.toFixed(1)}%</b></div>
              <div className="detalle-item"><span>Viento</span> <b>{seleccionada.viento_vel_ms?.toFixed(1)} m/s</b></div>
              <div className="detalle-item"><span>Presión</span> <b>{seleccionada.presion_hpa?.toFixed(1)} hPa</b></div>
            </div>
          </div>

          <div className="pronostico-horas">
            <h3>LAS PRÓXIMAS 24 HORAS</h3>
            <div className="tabla-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Hora</th>
                    {pronosticoExtendido.map((dato, i) => (
                      <th key={i}>
                        {new Date(dato.hora_validez).getUTCHours().toString().padStart(2, '0')}:00
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td></td>
                    {pronosticoExtendido.map((dato, i) => (
                      <td key={i} className="icono-celda">
                        {getIconoClima(dato.hora_validez, dato.temperatura_2m, dato.precipitacion_mm ?? 0)}
                      </td>
                    ))}
                  </tr>
                  <FilaPronostico label="Temp."   campo="temperatura_2m"  unidad="°"    className="temp-celda" />
                  <FilaPronostico label="Precip." campo="precipitacion_mm" unidad=" mm" />
                  <FilaPronostico label="Viento"  campo="viento_vel_ms"    unidad=" m/s" />
                  <FilaPronostico label="Humedad" campo="humedad_rel_pct"  unidad="%" />
                  <FilaPronostico label="Presión" campo="presion_hpa"      unidad=" hPa" />
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Radar & Pysteps toggle — top right */}
      <div className="radar-toggle">
        <button className={`radar-btn${radarActivo ? ' activo' : ''}`} onClick={toggleRadar}>
          🌧 {radarCargando ? 'Cargando...' : radarActivo ? 'Radar Precip. ON' : 'Radar de Precipitación'}
        </button>
        <button className={`pysteps-btn${pystepsActivo ? ' activo' : ''}`} onClick={togglePysteps}>
          ⚡ {pystepsCargando ? 'Cargando...' : pystepsActivo ? 'Pysteps ON' : 'Predicción Pysteps'}
        </button>
        <button className={`pysteps10min-btn${pysteps10minActivo ? ' activo' : ''}`} onClick={togglePysteps10min}>
          ⏱ {pysteps10minCargando ? 'Cargando...' : pysteps10minActivo ? 'Pysteps 10min ON' : 'Predicción 10-minutal'}
        </button>
        <button className={`acum-btn${acumActivo ? ' activo' : ''}`} onClick={toggleAcum}>
          📊 {acumCargando ? 'Cargando...' : acumActivo ? 'Acum. ON' : 'Acumulación Horaria'}
        </button>
        <button className={`com2602-btn${com2602Activo ? ' activo' : ''}`} onClick={toggleCom2602}>
          📡 {com2602Cargando ? 'Cargando...' : com2602Activo ? 'COM2602 ON' : 'Radar COM2602'}
        </button>
      </div>

      {/* Bottom stack — all bottom panels + slider in one fixed wrapper */}
      <div className="bottom-stack">

      {/* Radar player panel — bottom */}
      {radarActivo && (radarFrames.length > 0 || radarCargando) && (
        <div className="radar-panel">

          {/* Row 1: date picker + timestamp + controls */}
          <div className="radar-panel-top">
            <div className="radar-fecha-picker">
              <input
                type="datetime-local"
                className="radar-fecha-input"
                value={radarFechaPendiente}
                max={new Date().toISOString().slice(0, 16)}
                onChange={e => setRadarFechaPendiente(e.target.value)}
              />
              <button
                className="radar-set-btn"
                onClick={() => { setRadarFecha(radarFechaPendiente || null); setRadarPausado(false); }}
              >
                Set
              </button>
              {radarFecha && (
                <button className="radar-reciente-btn" onClick={() => { setRadarFecha(null); setRadarFechaPendiente(''); setRadarPausado(false); }}>
                  ↩
                </button>
              )}
            </div>
            <span className="radar-hora-actual">
              {radarCargando
                ? 'Cargando...'
                : new Date(radarFrames[radarIndice]?.fecha).toLocaleString('es-ES', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })
              }
            </span>
            <div className="radar-panel-controles">
              <button
                className="radar-ctrl-btn"
                title={radarPausado ? 'Reproducir' : 'Pausar'}
                onClick={() => setRadarPausado(p => !p)}
              >
                {radarPausado ? '▶' : '⏸'}
              </button>
              <select
                className="radar-velocidad"
                value={radarIntervalo}
                onChange={e => { setRadarIntervalo(Number(e.target.value)); setRadarPausado(false); }}
              >
                <option value={10}>10 min</option>
                <option value={20}>20 min</option>
                <option value={30}>30 min</option>
                <option value={40}>40 min</option>
                <option value={50}>50 min</option>
                <option value={60}>60 min</option>
              </select>
              <select
                className="radar-velocidad"
                value={radarVelocidad}
                onChange={e => setRadarVelocidad(Number(e.target.value))}
              >
                <option value={300}>Rápido</option>
                <option value={700}>Normal</option>
                <option value={1500}>Lento</option>
                <option value={3000}>Muy lento</option>
              </select>
            </div>
          </div>

          {/* Row 2: scrollable timeline */}
          <div className="radar-timeline" ref={radarTimelineRef}>
            {radarFrames.map((frame, i) => (
              <div
                key={i}
                className={`radar-seg${i === radarIndice ? ' activo' : ''}`}
                onClick={() => { setRadarIndice(i); setRadarPausado(true); }}
              >
                <div className="radar-seg-bar" />
                <span className="radar-seg-label">
                  {new Date(frame.fecha).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>

          {/* Row 3: precipitation legend */}
          <div className="radar-leyenda">
            <div className="radar-leyenda-grad" />
            <div className="radar-leyenda-labels">
              <span>1</span>
              <span>3</span>
              <span>10</span>
              <span>25</span>
              <span>65</span>
              <span>100</span>
              <span>300 mm/h</span>
            </div>
          </div>

        </div>
      )}

      {/* Pysteps player panel — bottom (offset if radar also visible) */}
      {pystepsActivo && (
        <div className="pysteps-panel">

          {/* Row 1: date picker + timestamp + controls */}
          <div className="pysteps-panel-top">
            <div className="pysteps-fecha-picker">
              <input
                type="date"
                className="pysteps-fecha-input"
                value={pystepsFechaPendiente}
                max={new Date().toISOString().slice(0, 10)}
                onChange={e => setPystepsFechaPendiente(e.target.value)}
              />
              <button
                className="pysteps-set-btn"
                onClick={() => { setPystepsFecha(pystepsFechaPendiente || null); setPystepsPausado(false); }}
              >
                Set
              </button>
              {pystepsFecha && (
                <button className="pysteps-reciente-btn" onClick={() => { setPystepsFecha(null); setPystepsFechaPendiente(''); setPystepsPausado(false); }}>
                  ↩
                </button>
              )}
            </div>
            <span className="pysteps-hora-actual">
              {pystepsCargando
                ? 'Cargando...'
                : pystepsFrames[pystepsIndice]
                ? new Date(pystepsFrames[pystepsIndice].fecha).toLocaleString('es-ES', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })
                : '--'
              }
            </span>
            <div className="pysteps-panel-controles">
              <button
                className="pysteps-ctrl-btn"
                title={pystepsPausado ? 'Reproducir' : 'Pausar'}
                onClick={() => setPystepsPausado(p => !p)}
              >
                {pystepsPausado ? '▶' : '⏸'}
              </button>
              <select
                className="pysteps-velocidad"
                value={pystepsVelocidad}
                onChange={e => setPystepsVelocidad(Number(e.target.value))}
              >
                <option value={300}>Rápido</option>
                <option value={700}>Normal</option>
                <option value={1500}>Lento</option>
                <option value={3000}>Muy lento</option>
              </select>
            </div>
          </div>

          {/* Show message when no data available */}
          {!pystepsCargando && pystepsFrames.length === 0
            ? <div className="pysteps-sin-datos">Sin datos para esta fecha</div>
            : <>
                {/* Row 2: scrollable timeline */}
                <div className="pysteps-timeline" ref={pystepsTimelineRef}>
                  {pystepsFrames.map((frame, i) => (
                    <div
                      key={i}
                      className={`pysteps-seg${i === pystepsIndice ? ' activo' : ''}`}
                      onClick={() => { setPystepsIndice(i); setPystepsPausado(true); }}
                    >
                      <div className="pysteps-seg-bar" />
                      <span className="pysteps-seg-label">
                        {new Date(frame.fecha).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Row 3: accumulated precipitation legend (mm) */}
                <div className="pysteps-leyenda">
                  <div className="pysteps-leyenda-grad" />
                  <div className="pysteps-leyenda-labels">
                    <span>0.1</span>
                    <span>0.5</span>
                    <span>1</span>
                    <span>2</span>
                    <span>5</span>
                    <span>10</span>
                    <span>50 mm</span>
                  </div>
                </div>
              </>
          }

        </div>
      )}

      {/* Pysteps 10-minutal player panel — bottom */}
      {pysteps10minActivo && (
        <div className="pysteps10min-panel">

          {/* Row 1: date picker + timestamp + controls */}
          <div className="pysteps10min-panel-top">
            <div className="pysteps10min-fecha-picker">
              <input
                type="date"
                className="pysteps10min-fecha-input"
                value={pysteps10minFechaPendiente}
                max={new Date().toISOString().slice(0, 10)}
                onChange={e => setPysteps10minFechaPendiente(e.target.value)}
              />
              <button
                className="pysteps10min-set-btn"
                onClick={() => { setPysteps10minFecha(pysteps10minFechaPendiente || null); setPysteps10minPausado(false); }}
              >
                Set
              </button>
              {pysteps10minFecha && (
                <button className="pysteps10min-reciente-btn" onClick={() => { setPysteps10minFecha(null); setPysteps10minFechaPendiente(''); setPysteps10minPausado(false); }}>
                  ↩
                </button>
              )}
            </div>
            <span className="pysteps10min-hora-actual">
              {pysteps10minCargando
                ? 'Cargando...'
                : pysteps10minFrames[pysteps10minIndice]
                ? new Date(pysteps10minFrames[pysteps10minIndice].fecha).toLocaleString('es-ES', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })
                : '--'
              }
            </span>
            <div className="pysteps10min-panel-controles">
              <button
                className="pysteps10min-ctrl-btn"
                title={pysteps10minPausado ? 'Reproducir' : 'Pausar'}
                onClick={() => setPysteps10minPausado(p => !p)}
              >
                {pysteps10minPausado ? '▶' : '⏸'}
              </button>
              <select
                className="pysteps10min-velocidad"
                value={pysteps10minVelocidad}
                onChange={e => setPysteps10minVelocidad(Number(e.target.value))}
              >
                <option value={300}>Rápido</option>
                <option value={700}>Normal</option>
                <option value={1500}>Lento</option>
                <option value={3000}>Muy lento</option>
              </select>
            </div>
          </div>

          {/* Show message when no data available */}
          {!pysteps10minCargando && pysteps10minFrames.length === 0
            ? <div className="pysteps10min-sin-datos">Sin datos para esta fecha</div>
            : <>
                {/* Row 2: scrollable timeline */}
                <div className="pysteps10min-timeline" ref={pysteps10minTimelineRef}>
                  {pysteps10minFrames.map((frame, i) => (
                    <div
                      key={i}
                      className={`pysteps10min-seg${i === pysteps10minIndice ? ' activo' : ''}`}
                      onClick={() => { setPysteps10minIndice(i); setPysteps10minPausado(true); }}
                    >
                      <div className="pysteps10min-seg-bar" />
                      <span className="pysteps10min-seg-label">
                        {new Date(frame.fecha).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Row 3: accumulated precipitation legend (mm, 10-min) */}
                <div className="pysteps10min-leyenda">
                  <div className="pysteps10min-leyenda-grad" />
                  <div className="pysteps10min-leyenda-labels">
                    <span>0.05</span>
                    <span>0.2</span>
                    <span>0.5</span>
                    <span>1</span>
                    <span>2</span>
                    <span>5</span>
                    <span>10 mm</span>
                  </div>
                </div>
              </>
          }

        </div>
      )}

      {/* Acumulacion player panel — bottom */}
      {acumActivo && (
        <div className="acum-panel">
          <div className="acum-panel-top">
            <div className="acum-fecha-picker">
              <input
                type="date"
                className="acum-fecha-input"
                value={acumFechaPendiente}
                max={new Date().toISOString().slice(0, 10)}
                onChange={e => setAcumFechaPendiente(e.target.value)}
              />
              <button
                className="acum-set-btn"
                onClick={() => { setAcumFecha(acumFechaPendiente || null); setAcumPausado(false); }}
              >
                Set
              </button>
              {acumFecha && (
                <button className="acum-reciente-btn" onClick={() => { setAcumFecha(null); setAcumFechaPendiente(''); setAcumPausado(false); }}>
                  ↩
                </button>
              )}
            </div>
            <span className="acum-hora-actual">
              {acumCargando
                ? 'Cargando...'
                : acumFrames[acumIndice]
                ? new Date(acumFrames[acumIndice].fecha).toLocaleString('es-ES', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })
                : '--'
              }
            </span>
            <div className="acum-panel-controles">
              <button className="acum-ctrl-btn" onClick={() => setAcumPausado(p => !p)}>
                {acumPausado ? '▶' : '⏸'}
              </button>
              <select className="acum-velocidad" value={acumVelocidad} onChange={e => setAcumVelocidad(Number(e.target.value))}>
                <option value={300}>Rápido</option>
                <option value={700}>Normal</option>
                <option value={1500}>Lento</option>
                <option value={3000}>Muy lento</option>
              </select>
            </div>
          </div>
          {!acumCargando && acumFrames.length === 0
            ? <div className="acum-sin-datos">Sin datos para esta fecha</div>
            : <>
                <div className="acum-timeline" ref={acumTimelineRef}>
                  {acumFrames.map((frame, i) => (
                    <div
                      key={i}
                      className={`acum-seg${i === acumIndice ? ' activo' : ''}`}
                      onClick={() => { setAcumIndice(i); setAcumPausado(true); }}
                    >
                      <div className="acum-seg-bar" />
                      <span className="acum-seg-label">
                        {new Date(frame.fecha).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="acum-leyenda">
                  <div className="acum-leyenda-grad" />
                  <div className="acum-leyenda-labels">
                    <span>0.1</span><span>0.5</span><span>1</span><span>2</span>
                    <span>5</span><span>10</span><span>50 mm</span>
                  </div>
                </div>
              </>
          }
        </div>
      )}

      {/* COM2602 player panel — bottom */}
      {com2602Activo && (
        <div className="com2602-panel">
          <div className="com2602-panel-top">
            <div className="com2602-fecha-picker">
              <input
                type="date"
                className="com2602-fecha-input"
                value={com2602FechaPendiente}
                max={new Date().toISOString().slice(0, 10)}
                onChange={e => setCom2602FechaPendiente(e.target.value)}
              />
              <button
                className="com2602-set-btn"
                onClick={() => { setCom2602Fecha(com2602FechaPendiente || null); setCom2602Pausado(false); }}
              >
                Set
              </button>
              {com2602Fecha && (
                <button className="com2602-reciente-btn" onClick={() => { setCom2602Fecha(null); setCom2602FechaPendiente(''); setCom2602Pausado(false); }}>
                  ↩
                </button>
              )}
            </div>
            <span className="com2602-hora-actual">
              {com2602Cargando
                ? 'Cargando...'
                : com2602Frames[com2602Indice]
                ? new Date(com2602Frames[com2602Indice].fecha).toLocaleString('es-ES', {
                    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
                  })
                : '--'
              }
            </span>
            <div className="com2602-panel-controles">
              <button className="com2602-ctrl-btn" onClick={() => setCom2602Pausado(p => !p)}>
                {com2602Pausado ? '▶' : '⏸'}
              </button>
              <select className="com2602-velocidad" value={com2602Velocidad} onChange={e => setCom2602Velocidad(Number(e.target.value))}>
                <option value={300}>Rápido</option>
                <option value={700}>Normal</option>
                <option value={1500}>Lento</option>
                <option value={3000}>Muy lento</option>
              </select>
            </div>
          </div>
          {!com2602Cargando && com2602Frames.length === 0
            ? <div className="com2602-sin-datos">Sin datos para esta fecha</div>
            : <>
                <div className="com2602-timeline" ref={com2602TimelineRef}>
                  {com2602Frames.map((frame, i) => (
                    <div
                      key={i}
                      className={`com2602-seg${i === com2602Indice ? ' activo' : ''}`}
                      onClick={() => { setCom2602Indice(i); setCom2602Pausado(true); }}
                    >
                      <div className="com2602-seg-bar" />
                      <span className="com2602-seg-label">
                        {new Date(frame.fecha).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="com2602-leyenda">
                  <div className="com2602-leyenda-grad" />
                  <div className="com2602-leyenda-labels">
                    <span>0.1</span><span>0.5</span><span>1</span><span>2</span>
                    <span>5</span><span>10</span><span>50 mm/h</span>
                  </div>
                </div>
              </>
          }
        </div>
      )}

      <div className="contenedor-slider">
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px'}}>
          <div>
            <strong>+{hora}h</strong>
            <span style={{marginLeft: '10px', fontSize: '0.85em', color: '#666'}}>
              {new Date(Date.now() + hora * 3600000).toLocaleString('es-ES', {month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'})}
            </span>
          </div>
          <div style={{display: 'flex', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end'}}>
            {[6, 12, 24, 48, 72].map(h => (
              <button
                key={h}
                onClick={() => setHora(h)}
                style={{
                  padding: '5px 12px',
                  fontSize: '0.8rem',
                  border: hora === h ? '2px solid #3498db' : '1px solid #ccc',
                  backgroundColor: hora === h ? '#e3f2fd' : '#f9f9f9',
                  borderRadius: '5px',
                  cursor: 'pointer',
                  fontWeight: hora === h ? '700' : '500',
                  transition: 'all 0.2s',
                  color: hora === h ? '#2196f3' : '#333'
                }}
              >
                {h}h
              </button>
            ))}
          </div>
        </div>
        <input
          type="range" min="0" max="240"
          value={hora}
          onChange={(e) => setHora(parseInt(e.target.value))}
          id="time-slider"
        />
      </div>

      </div>{/* end .bottom-stack */}
    </div>
  );
}

export default App;
