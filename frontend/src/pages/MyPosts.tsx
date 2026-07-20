import { useState, useEffect } from 'react';
import { platformApi, type PostHistory } from '../api/client';

interface Props {
  token: string;
}

export default function MyPosts({ token }: Props) {
  const [posts, setPosts] = useState<PostHistory[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [cachedAt, setCachedAt] = useState('');
  const [error, setError] = useState('');
  const [commentPostId, setCommentPostId] = useState<string | null>(null);
  const [commentContent, setCommentContent] = useState('');
  const [commentLoading, setCommentLoading] = useState(false);
  const [commentResult, setCommentResult] = useState<{ success: boolean; message: string } | null>(null);

  async function fetchPosts(refresh = false) {
    if (refresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError('');
    try {
      const resp = await platformApi.getPosts('xueqiu', token, refresh);
      setPosts(resp.posts);
      if (resp.cached_at) {
        setCachedAt(resp.cached_at);
      }
      if (resp.error) {
        setError(resp.error);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    fetchPosts();
  }, [token]);

  async function handleCommentSubmit(post: PostHistory) {
    if (!commentContent.trim()) return;
    setCommentLoading(true);
    setCommentResult(null);
    try {
      const resp = await platformApi.createComment('xueqiu', commentContent, token, undefined, post.url || undefined, post.title || undefined);
      setCommentResult({
        success: resp.success,
        message: resp.success ? (resp.message || '评论成功') : (resp.error || '评论失败'),
      });
      if (resp.success) {
        setCommentContent('');
        setCommentPostId(null);
      }
    } catch (err: unknown) {
      setCommentResult({
        success: false,
        message: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setCommentLoading(false);
    }
  }

  function formatCachedAt(isoStr: string): string {
    if (!isoStr) return '';
    try {
      const d = new Date(isoStr);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  return (
    <div className="my-posts-page">
      <div className="my-posts-header">
        <div className="my-posts-info">
          {cachedAt && (
            <span className="cache-hint">
              更新于 {formatCachedAt(cachedAt)}
            </span>
          )}
        </div>
        <button
          className="btn-refresh"
          onClick={() => fetchPosts(true)}
          disabled={refreshing}
        >
          {refreshing ? '刷新中...' : '刷新'}
        </button>
      </div>

      {error && <div className="my-posts-error">{error}</div>}

      {loading && <p className="selection-loading">加载中...</p>}

      {!loading && posts.length === 0 && !error && (
        <p className="selection-empty">暂无帖子</p>
      )}

      {!loading && posts.length > 0 && (
        <div className="post-history-list">
          {posts.map(post => (
            <div key={post.post_id} className="post-history-card">
              <div className="phc-header">
                <span className="phc-title">{post.title || '无标题'}</span>
                <span className="phc-date">
                  {post.created_at ? new Date(post.created_at).toLocaleDateString('zh-CN') : ''}
                </span>
              </div>
              {post.url && (
                <a href={post.url} target="_blank" rel="noopener noreferrer" className="phc-link">
                  查看帖子
                </a>
              )}
              <div className="phc-actions">
                {commentPostId === post.post_id ? (
                  <div className="comment-form">
                    <textarea
                      className="comment-textarea"
                      placeholder="输入评论内容..."
                      value={commentContent}
                      onChange={e => setCommentContent(e.target.value)}
                      rows={3}
                    />
                    <div className="comment-form-footer">
                      <button
                        className="btn-link"
                        onClick={() => { setCommentPostId(null); setCommentContent(''); setCommentResult(null); }}
                      >
                        取消
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleCommentSubmit(post)}
                        disabled={!commentContent.trim() || commentLoading}
                      >
                        {commentLoading ? '提交中...' : '发布评论'}
                      </button>
                    </div>
                    {commentResult && (
                      <div className={`comment-result ${commentResult.success ? 'result-ok' : 'result-fail'}`}>
                        {commentResult.message}
                      </div>
                    )}
                  </div>
                ) : (
                  <button
                    className="btn-comment"
                    onClick={() => { setCommentPostId(post.post_id); setCommentContent(''); setCommentResult(null); }}
                  >
                    评论
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
