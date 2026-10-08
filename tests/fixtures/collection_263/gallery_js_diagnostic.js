window.addEventListener('DOMContentLoaded', () => {
    const track = document.getElementById('galleryThumbnailTrack');
    const preview = document.getElementById('galleryPreview');
    const previous = document.getElementById('galleryPrevious');
    const next = document.getElementById('galleryNext');
    const status = document.getElementById('galleryStatus');

    if (!track || !preview || !previous || !next || !status) return;

    // 画像の総枚数：追加したら、この数値を変更
    // ギャラリー画像の枚数
    const imageCount = 5;

    // プレビュー画像とリスト画像を対応させる
    const images = Array.from({ length: imageCount }, (_, index) => {
        const number = index + 1;

        return {
            preview: `common/image/spss_g_${number}.webp`,
            thumbnail: `common/image/spss_g_thum_${number}.webp`,
            alt: `ギャラリー画像 ${index + 1}`
        };
    });

    const visibleCount = 6;
    const reducedMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)'
    );

    track.style.setProperty('--gallery-visible', visibleCount);

    let selectedIndex = 0;

    // 前後に複製を置き、末尾から先頭へも滑らかにつなぐ
    let cursor = images.length;
    let moving = false;
    let finishTimer = null;
    let restoreThumbnailFocus = false;

    const thumbnails = [];

    track.replaceChildren();

    for (let copy = 0; copy < 4; copy++) {
        images.forEach((image, index) => {
            const physicalIndex = thumbnails.length;
            const button = document.createElement('button');

            button.type = 'button';
            button.className = 'gallery-thumbnail';
            button.setAttribute('aria-label', image.alt);
            button.setAttribute('aria-pressed', 'false');

            const img = document.createElement('img');
            img.src = image.thumbnail;
            img.alt = '';
            img.draggable = false;

            button.appendChild(img);

            button.addEventListener('click', () => {
                if (moving) return;

                const distance = physicalIndex - cursor;

                if (distance === 0) return;

                move(distance, true);
            });

            thumbnails.push(button);
            track.appendChild(button);
        });
    }

    function getStep() {
        const width = thumbnails[0].getBoundingClientRect().width;
        const gap = parseFloat(getComputedStyle(track).columnGap) || 0;

        return width + gap;
    }

    function updateThumbnailStates() {
        thumbnails.forEach((button, index) => {
            const visible =
                index >= cursor &&
                index < cursor + visibleCount;

            button.tabIndex = visible ? 0 : -1;
            button.setAttribute('aria-hidden', String(!visible));
            button.setAttribute(
                'aria-pressed',
                String(index === cursor)
            );
        });
    }

    function positionTrack(animate) {
        track.style.transition =
            animate && !reducedMotion.matches
                ? 'transform 0.35s ease'
                : 'none';

        track.style.transform =
            `translateX(${-cursor * getStep()}px)`;
    }

    function updatePreview() {
        const image = images[selectedIndex];

        preview.src = image.preview;
        preview.alt = image.alt;

        status.textContent =
            `全${images.length}枚中、${selectedIndex + 1}枚目`;
    }

    function finishMove() {
        clearTimeout(finishTimer);
        finishTimer = null;

        // 同じ見た目の中央の複製へ戻す
        cursor = images.length + selectedIndex;
        positionTrack(false);
        updateThumbnailStates();

        moving = false;

        if (restoreThumbnailFocus) {
            thumbnails[cursor].focus({ preventScroll: true });
            restoreThumbnailFocus = false;
        }
    }

    function move(distance, fromThumbnail = false) {
        if (moving || images.length === 1) return;

        moving = true;
        restoreThumbnailFocus = fromThumbnail;

        // 直前の位置を確定してからアニメーションを開始
        track.getBoundingClientRect();

        cursor += distance;

        selectedIndex =
            ((selectedIndex + distance) % images.length + images.length)
            % images.length;

        updatePreview();
        updateThumbnailStates();
        positionTrack(true);

        if (reducedMotion.matches) {
            finishMove();
        } else {
            // transitionendが発生しない場合にも解除する
            finishTimer = setTimeout(finishMove, 450);
        }
    }

    track.addEventListener('transitionend', event => {
        if (
            event.target === track &&
            event.propertyName === 'transform' &&
            moving
        ) {
            finishMove();
        }
    });

    previous.addEventListener('click', () => move(-1));
    next.addEventListener('click', () => move(1));

    // スマホ：サムネイルと表示画像を左右スワイプで切り替える
    const gallerySwipeMedia = window.matchMedia('(max-width: 899px)');

    function enableGallerySwipe(area) {
        if (!area) return;

        let start = null;
        let blockClickUntil = 0;

        area.addEventListener('touchstart', event => {
            start = null;

            if (
                !gallerySwipeMedia.matches ||
                event.touches.length !== 1 ||
                moving ||
                images.length < 2
            ) {
                return;
            }

            blockClickUntil = 0;

            const touch = event.touches[0];

            start = {
                id: touch.identifier,
                x: touch.clientX,
                y: touch.clientY,
                horizontal: false
            };
        }, { passive: true });

        area.addEventListener('touchmove', event => {
            if (!start) return;

            // 2本指の操作は拡大などに使う
            if (event.touches.length !== 1) {
                start = null;
                return;
            }

            const touch = event.touches[0];
            if (touch.identifier !== start.id) return;

            const dx = touch.clientX - start.x;
            const dy = touch.clientY - start.y;

            if (!start.horizontal) {
                // 小さな指の揺れは無視
                if (Math.max(Math.abs(dx), Math.abs(dy)) < 10) return;

                // 縦方向の操作はページスクロールに任せる
                if (Math.abs(dy) >= Math.abs(dx)) {
                    start = null;
                    return;
                }

                // 横方向と判定できるまで待つ
                if (Math.abs(dx) < Math.abs(dy) * 1.5) return;

                start.horizontal = true;
            }

            // 横スワイプ中だけブラウザの既定動作を抑える
            if (event.cancelable) {
                event.preventDefault();
            }

            blockClickUntil = performance.now() + 500;
        }, { passive: false });

        area.addEventListener('touchend', event => {
            if (!start) return;

            const touch = Array.from(event.changedTouches).find(
                item => item.identifier === start.id
            );

            if (!touch) return;

            const gesture = start;
            start = null;

            if (!gallerySwipeMedia.matches) return;

            const dx = touch.clientX - gesture.x;
            const dy = touch.clientY - gesture.y;

            // 横に40px以上動かしたら切り替える（時間制限なし）
            if (
                Math.abs(dx) < 40 ||
                Math.abs(dx) < Math.abs(dy) * 1.5
            ) {
                return;
            }

            blockClickUntil = performance.now() + 500;

            // 左へ払うと次、右へ払うと前
            move(dx < 0 ? 1 : -1);
        }, { passive: true });

        area.addEventListener('touchcancel', () => {
            start = null;
        }, { passive: true });

        // スワイプ後のサムネイルの誤クリックを防ぐ
        area.addEventListener('click', event => {
            if (
                event.detail !== 0 &&
                performance.now() < blockClickUntil
            ) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }, { capture: true });
    }

    enableGallerySwipe(track.closest('.gallery-thumbnails'));
    enableGallerySwipe(preview.closest('.gallery-preview-frame'));

    previous.disabled = images.length === 1;
    next.disabled = images.length === 1;

    // 表示幅が変わっても左端を合わせ直す
    const observer = new ResizeObserver(() => {
        finishMove();
    });

    observer.observe(track.parentElement);

    reducedMotion.addEventListener('change', finishMove);

    updatePreview();
    updateThumbnailStates();
    positionTrack(false);
});