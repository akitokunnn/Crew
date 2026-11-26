"use strict";
// Leader look: custom color and avatar shared by the sidebar, cards and chat.
// Every uploaded picture is cropped square and re-encoded here before upload;
// the server only accepts small images, so an unprocessed file cannot slip in.
(function () {
  const SIZE = 256, MAX_BYTES = 200 * 1024, MAX_INPUT = 30 * 1024 * 1024;
  const avatarUrl = person => person && person.avatar_version
    ? `/api/session/avatar?${new URLSearchParams({session_id:person.session_id, v:String(person.avatar_version)})}` : null;
  async function decode(file) {
    if (typeof createImageBitmap === "function") {
      try { return await createImageBitmap(file); } catch { /* fall back to <img>, which decodes more formats in some browsers */ }
    }
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      return image;
    } finally { URL.revokeObjectURL(url); }
  }
  const encode = (canvas, type, quality) => new Promise(resolve => canvas.toBlob(resolve, type, quality));
  async function compressSquare(file) {
    if (!file || !/^image\//.test(file.type || "")) throw new Error("请选择图片文件。");
    if (file.size > MAX_INPUT) throw new Error("图片超过 30 MB，请换一张小一点的。");
    let image;
    try { image = await decode(file); } catch { throw new Error("这张图片无法读取，请换 JPG、PNG 或 WebP。"); }
    const width = image.width || image.naturalWidth, height = image.height || image.naturalHeight;
    if (!width || !height) throw new Error("这张图片无法读取，请换 JPG、PNG 或 WebP。");
    const side = Math.min(width, height), size = Math.min(SIZE, side), canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const draw = canvas.getContext("2d");
    draw.imageSmoothingQuality = "high";
    draw.drawImage(image, (width - side) / 2, (height - side) / 2, side, side, 0, 0, size, size);
    image.close?.();
    // WebP keeps photos sharp at a fraction of PNG; browsers that cannot encode it hand back PNG, so use JPEG there.
    for (const quality of [0.86, 0.78, 0.68, 0.58]) {
      let blob = await encode(canvas, "image/webp", quality);
      if (!blob || blob.type !== "image/webp") blob = await encode(canvas, "image/jpeg", quality);
      if (blob && blob.size <= MAX_BYTES) return {blob, type:blob.type, size:blob.size, width:size, height:size};
    }
    throw new Error("压缩后仍然太大，请换一张图片。");
  }
  function toBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
      reader.onerror = () => reject(reader.error || new Error("读取压缩后的图片失败。"));
      reader.readAsDataURL(blob);
    });
  }
  window.CrewLook = {SIZE, MAX_BYTES, avatarUrl, compressSquare, toBase64};
})();
