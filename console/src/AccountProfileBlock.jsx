import { useEffect, useRef, useState } from 'react';

import AccountAvatar from './AccountAvatar.jsx';
import AvatarCrop from './AvatarCrop.jsx';
import {
  ensureProfile,
  saveNickname,
  sendAvatar,
  syncProfileFromSite,
  useProfile,
} from './profile.js';

/* 账号卡里的「头像 + 昵称」块：圆形头像、显示名、邮箱小字，
   以及「改昵称 / 换头像 / 从官网同步」三个动作。
   头像与昵称来自共享 store（profile.js），所以顶栏/侧栏/这里永远一致。

   2026-10-06 修（老板反馈"已经登陆了但还是显示未登录，头像昵称也没同步"）：
   1) 一进这个页签就真去读 GET /api/account/profile（后端会顺手跟官网同步），
      不再等用户点「从官网同步」；
   2) 有 token 时显示真资料或「读取中…」，绝不再冒「先登录…」那种自相矛盾的话；
   3) 资料没到之前拿登录邮箱兜底（显示邮箱前缀），不写死「—」。 */

const mailPrefix = (value) => String(value || '').split('@')[0].trim();

export default function AccountProfileBlock({ loggedIn, onMsg = () => {}, email = '' }) {
  const { profile, loading, error } = useProfile();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  /* 选完图先停在裁剪框里，确认了才上传（老板 2026-10-06：长照片没有 1:1 剪切）。 */
  const [crop, setCrop] = useState(null);
  const fileRef = useRef(null);
  const nameInputRef = useRef(null);

  useEffect(() => {
    if (editing && nameInputRef.current) nameInputRef.current.focus();
  }, [editing]);

  // 进页签（或登录态刚变）就读一次资料：open() 里带 force，保证每次进来都是新的
  useEffect(() => {
    ensureProfile(Boolean(loggedIn), true);
  }, [loggedIn]);

  if (!loggedIn) {
    return (
      <div className="acct-card">
        <AccountAvatar profile={null} size={52} />
        <div className="acct-card__body">
          <b className="acct-card__name">未登录</b>
          <span className="acct-card__mail">登录后这里会显示官网的头像和昵称。</span>
        </div>
      </div>
    );
  }

  const mail = profile?.email || email || '';
  const display = profile?.display_name || profile?.nickname
    || mailPrefix(mail) || '星群账号';
  const pending = loading && !profile;

  const submitNickname = async () => {
    setBusy(true);
    const res = await saveNickname(draft);
    setBusy(false);
    setEditing(false);
    onMsg(res.message);
  };

  const resetFile = () => {
    if (fileRef.current) fileRef.current.value = '';   // 同一张图能再选一次
  };

  /* 选完图**不**直接上传：先开裁剪框，让用户自己选位置和大小。 */
  const pickAvatar = (file) => {
    if (!file) return;
    if (!String(file.type || '').startsWith('image/')) {
      onMsg('这个文件不是图片，换一张图片试试');
      resetFile();
      return;
    }
    setCrop(file);
  };

  const cancelCrop = () => {
    setCrop(null);
    resetFile();                                       // 取消 = 一个请求都不发
  };

  const confirmCrop = async (dataUrl) => {
    setCrop(null);
    resetFile();
    setBusy(true);
    onMsg('正在上传新头像…');
    try {
      const res = await sendAvatar(dataUrl);
      onMsg(res.message);
    } catch (e) {
      onMsg(String((e && e.message) || e || '换头像失败'));
    } finally {
      setBusy(false);
    }
  };

  const doSync = async () => {
    setBusy(true);
    const res = await syncProfileFromSite();
    setBusy(false);
    onMsg(res.message);
  };

  return (
    <div className="acct-card">
      <AccountAvatar profile={profile || { display_name: display }} size={52} />
      <div className="acct-card__body">
        <b className="acct-card__name">{display}</b>
        {mail && <span className="acct-card__mail">{mail}</span>}
        {profile?.signature && <span className="acct-card__sign">{profile.signature}</span>}
        {pending && <span className="acct-card__mail">账号资料读取中…</span>}
        {error && !pending && <span className="acct-card__err">{error}</span>}
      </div>

      <div className="acct-card__actions">
        {editing ? (
          <div className="p-row">
            <input
              className="p-input"
              ref={nameInputRef}
              value={draft}
              placeholder="新昵称（留空 = 显示邮箱前缀）"
              spellCheck={false}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submitNickname();
                if (e.key === 'Escape') setEditing(false);
              }}
            />
            <button type="button" className="btn btn--primary" disabled={busy} onClick={submitNickname}>
              {busy ? '保存中…' : '保存'}
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => setEditing(false)}>
              取消
            </button>
          </div>
        ) : (
          <>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                setDraft(String(profile?.nickname || ''));
                setEditing(true);
              }}
            >
              改昵称
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              title="选一张本地图片，自己选位置裁成正方形再上传（不超过 4MB）"
              onClick={() => fileRef.current && fileRef.current.click()}
            >
              {busy ? '处理中…' : '换头像'}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              title="在官网改过头像或昵称，点这里同步回本机。"
              onClick={doSync}
            >
              从官网同步
            </button>
          </>
        )}
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: 'none' }}
          onChange={(e) => pickAvatar(e.target.files && e.target.files[0])}
        />
      </div>

      {crop && (
        <AvatarCrop
          file={crop}
          busy={busy}
          onCancel={cancelCrop}
          onConfirm={confirmCrop}
        />
      )}
    </div>
  );
}
