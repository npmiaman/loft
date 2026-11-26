import { useEffect, useState } from 'react';
import * as store from './storage';
import type { Lead } from './types';

type KV = store.KV;

export function useStore<K extends keyof KV>(key: K, initial: KV[K]) {
  const [val, setVal] = useState<KV[K]>(initial);
  useEffect(() => {
    let alive = true;
    store.get(key).then((v) => alive && setVal(v));
    const h = (ch: Record<string, chrome.storage.StorageChange>, area: string) => {
      const c = ch[key as string];
      if (area === 'local' && c) setVal((c.newValue ?? initial) as KV[K]);
    };
    chrome.storage.onChanged.addListener(h);
    return () => {
      alive = false;
      chrome.storage.onChanged.removeListener(h);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const update = (v: KV[K]) => {
    setVal(v);
    return store.set(key, v);
  };
  return [val, update] as const;
}

export function useLeads() {
  const [leads, setLeads] = useState<Lead[]>([]);
  useEffect(() => {
    let alive = true;
    const load = () => store.listLeads().then((l) => alive && setLeads(l));
    load();
    const h = (ch: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && Object.keys(ch).some((k) => k.startsWith('lead:'))) load();
    };
    chrome.storage.onChanged.addListener(h);
    return () => {
      alive = false;
      chrome.storage.onChanged.removeListener(h);
    };
  }, []);
  return leads;
}
