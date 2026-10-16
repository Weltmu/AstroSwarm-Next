import { useSyncExternalStore } from 'react';

/* 「你想要什么功能？」那句话要**带到插件页**去用（不是只跳页）。
   很小的一份共享状态：概览页的输入框写进来，插件页的插件工坊读出来。
   放进模块级 store 是因为两个页面是两个组件，中间隔着 App 的路由。 */

let idea = '';
const listeners = new Set();

function emit() {
  listeners.forEach((fn) => fn());
}

export function setIdea(text) {
  idea = String(text == null ? '' : text);
  emit();
}

export function clearIdea() {
  setIdea('');
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function snapshot() {
  return idea;
}

export function useIdea() {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** 给测试用：直接读当前那句话（不用进 React 渲染）。 */
export function getIdea() {
  return idea;
}
