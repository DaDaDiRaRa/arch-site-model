# geometry JSON(로컬 미터) → SketchUp 엔티티 조립 (B1: 지형 + 건물).
#   - 지형: Geom::PolygonMesh → add_faces_from_mesh (대량 면 일괄 생성 + 소프트 엣지)
#   - 건물: 바닥면(홀 포함) + 벽(수직 쿼드) + 윗면(n각형)을 실제 면(add_face)으로 직접 생성.
#           pushpull을 쓰지 않는다 — 밀집지(수백 동)에서 pushpull 반복이 렉·크래시를 내던
#           문제 회피. 삼각 메쉬(add_faces_from_mesh)가 아니라 진짜 면이라 대각선 없이 깨끗하다.
#           (각 건물이 독립 그룹이라 add_face 교차 비용은 그 건물 면들에만 국한 → 빠름.)
# 단위: 백엔드는 미터, SketchUp 내부는 인치 → ×M2I. 좌표축 X=동, Y=북, Z=높이.

module ArchSiteModel
  module Builder
    M2I = 39.3701 # meter → inch

    C_BUILDING = [70, 130, 180].freeze  # steel blue
    C_FLAGGED  = [210, 120, 60].freeze  # orange (층수 미확인)
    C_TERRAIN  = [120, 150, 90].freeze  # olive green

    # 단일 조립(소반경): root 그룹 생성 + 전체 조립 + zoom. 반환: 생성된 건물 수.
    # geometry: {"buildings"=>[...], "terrain"=>{...}|nil}
    # ortho_png/ortho_extent: 정사영상 PNG 경로 + 로컬미터 extent[x0,y0,x1,y1](선택)
    def self.build(geometry, _warnings = [], ortho_png = nil, ortho_extent = nil)
      model = Sketchup.active_model
      count = 0
      model.start_operation("대지모델 생성", true)
      begin
        root = model.active_entities.add_group
        root.name = "arch-site-model"
        count = build_into(model, root.entities, geometry, ortho_png, ortho_extent)
        model.commit_operation
        model.active_view.zoom_extents
      rescue StandardError => e
        model.abort_operation
        UI.messagebox("모델 조립 오류: #{e.message}")
        raise
      end
      count
    end

    # 지형 + 건물을 주어진 entities에 조립(단일/타일 공통). 반환: 건물 수.
    def self.build_into(model, parent_ents, geometry, ortho_png = nil, ortho_extent = nil)
      if geometry["terrain"]
        build_terrain(model, parent_ents, geometry["terrain"], ortho_png, ortho_extent)
      end
      build_buildings(model, parent_ents, geometry["buildings"] || [])
    end

    # 타일 1개를 root 아래 서브그룹에 조립(자체 operation, zoom 없음).
    #
    # root_holder = {group: Group|nil} (호출 간 공유되는 가변 홀더). root는 **첫 타일
    # 내용과 같은 operation에서** 생성한다 — 빈 그룹을 미리 만들어 commit하면 SketchUp이
    # 자동 삭제해 다음 호출에서 "reference to deleted Group"이 나기 때문(내용과 함께
    # 태어나야 살아남는다). deleted? 가드로 만약 비어서 purge됐으면 재생성한다.
    # zoom_extents는 main이 마지막에 1회. 반환: 이 타일의 건물 수.
    def self.build_tile(model, root_holder, geometry, tile_label)
      count = 0
      model.start_operation("타일 #{tile_label}", true)
      begin
        root = root_holder[:group]
        if root.nil? || root.deleted?
          root = model.active_entities.add_group
          root.name = "arch-site-model"
          root_holder[:group] = root
        end
        tile_grp = root.entities.add_group
        tile_grp.name = "tile #{tile_label}"
        count = build_into(model, tile_grp.entities, geometry)
        model.commit_operation
      rescue StandardError
        model.abort_operation
        raise
      end
      count
    end

    def self.build_terrain(model, parent_ents, terrain, ortho_png = nil, ortho_extent = nil)
      verts = terrain["vertices"] || []
      tris = terrain["triangles"] || []
      return if verts.empty? || tris.empty?

      grp = parent_ents.add_group
      grp.name = "terrain"
      grp.layer = tag(model, "terrain")

      mesh = Geom::PolygonMesh.new(verts.length, tris.length)
      # add_point는 동일 좌표를 중복 제거하고 기존 1-based 인덱스를 돌려줄 수 있으므로
      # 반환 인덱스를 그대로 매핑에 쓴다(순차 +1 가정에 의존하지 않음).
      idx = verts.map { |v| mesh.add_point(Geom::Point3d.new(v[0] * M2I, v[1] * M2I, v[2] * M2I)) }
      tris.each do |t|
        a = idx[t[0]]; b = idx[t[1]]; c = idx[t[2]]
        next if a.nil? || b.nil? || c.nil? || a == b || b == c || a == c
        mesh.add_polygon(a, b, c)
      end

      smooth = Geom::PolygonMesh::AUTO_SOFTEN | Geom::PolygonMesh::SMOOTH_SOFT_EDGES
      mat = material(model, "asm_terrain", C_TERRAIN)
      grp.entities.add_faces_from_mesh(mesh, smooth, mat, mat)
      # 타일 경계·테두리에 남는 하드 엣지(지형에 보이는 격자선)를 숨긴다: 지형 그룹의
      # 모든 엣지를 soft+smooth → 선이 안 보이고 매끈하게 셰이딩(겹친 타일도 선 없이 blend).
      grp.entities.grep(Sketchup::Edge).each { |e| e.soft = true; e.smooth = true }

      drape_ortho(model, grp, ortho_png, ortho_extent) if ortho_png && ortho_extent
    end

    # 정사영상 PNG를 지형에 위→아래 평면투영으로 드레이프(B2).
    # extent = [x0,y0,x1,y1] 로컬 미터(지형 정점과 동일 좌표계). 각 삼각형 정점의
    # (x,y)를 이미지 UV[0,1]로 매핑 → position_material로 텍스처 투영. Z는 UV 무영향
    # (정사영상 특성과 정확히 일치). 실패한 면은 조용히 건너뛴다.
    def self.drape_ortho(model, terrain_grp, png_path, extent)
      return unless png_path && extent && extent.length == 4
      unless File.exist?(png_path)
        puts "[ortho] PNG 파일 없음: #{png_path}"
        return
      end
      x0, y0, x1, y1 = extent.map(&:to_f)
      dx = x1 - x0
      dy = y1 - y0
      sz = (File.size(png_path) rescue "?")
      puts "[ortho] drape 시작: #{png_path} (#{sz}B), extent=#{extent.inspect}"
      return if dx.abs < 1e-6 || dy.abs < 1e-6

      mat = model.materials.add("asm_ortho")
      mat.texture = png_path
      puts "[ortho] material.texture=#{mat.texture ? mat.texture.filename : 'nil(텍스처 로드 실패)'}"

      faces = terrain_grp.entities.grep(Sketchup::Face)
      applied = 0
      failed = 0
      faces.each do |face|
        vs = face.outer_loop.vertices
        next if vs.length < 3
        pts_uvs = []
        vs.first(4).each do |v|
          p = v.position
          u = (p.x / M2I - x0) / dx
          w = (p.y / M2I - y0) / dy
          pts_uvs << p << Geom::Point3d.new(u, w, 0)
        end
        begin
          r = face.position_material(mat, pts_uvs, true)
          r ? (applied += 1) : (failed += 1)
        rescue StandardError => e
          failed += 1
          puts "[ortho] position_material 예외(첫 1건): #{e.message}" if failed == 1
        end
      end
      puts "[ortho] 완료: 면 #{faces.length}, 적용 #{applied}, 실패 #{failed}"
      # "텍스처가 표시된 음영"으로 전환 → 위성사진이 뷰포트에 보이게(안 하면 재질
      # 평균색만 보여 초록으로 보임). 텍스처를 실제로 입힌 경우에만 켠다.
      begin
        model.rendering_options["Texture"] = true if applied > 0
      rescue StandardError
        nil
      end
    rescue StandardError => e
      puts "[ortho] drape 오류: #{e.message}"
    end

    def self.build_buildings(model, parent_ents, buildings)
      mat_n = material(model, "asm_building", C_BUILDING)
      mat_f = material(model, "asm_building_unverified", C_FLAGGED)
      tag_n = tag(model, "buildings")
      tag_f = tag(model, "buildings_unverified")
      built = 0

      buildings.each do |b|
        begin
          built += 1 if build_one_building(parent_ents, b, mat_n, mat_f, tag_n, tag_f)
        rescue StandardError
          next # 한 동 실패는 건너뛰고 계속(수백 동 중 불량 footprint가 전체를 막지 않게)
        end
      end
      built
    end

    # 건물 1동: 바닥면(홀 포함) + 벽(수직 쿼드) + 윗면(n각형)을 실제 면으로. 성공 시 true.
    def self.build_one_building(parent_ents, b, mat_n, mat_f, tag_n, tag_f)
      fp = b["footprint"] || []
      height = b["height"].to_f
      return false if fp.length < 3 || height <= 0

      flagged = b["flagged"] == true
      mat = flagged ? mat_f : mat_n
      grp = parent_ents.add_group
      grp.layer = flagged ? tag_f : tag_n
      grp.name = flagged ? "building [층수미확인]" : "building"
      ents = grp.entities
      base = b["base_z"].to_f * M2I
      h = height * M2I

      # 1) 바닥 면(구멍 포함) — add_face가 오목/홀을 네이티브 n각형으로 처리.
      pts = fp.map { |p| Geom::Point3d.new(p[0] * M2I, p[1] * M2I, base) }
      base_face = safe_add_face(ents, pts)
      return false if base_face.nil?
      (b["holes"] || []).each do |hole|
        next if hole.length < 3
        hpts = hole.map { |p| Geom::Point3d.new(p[0] * M2I, p[1] * M2I, base) }
        hface = safe_add_face(ents, hpts)
        hface.erase! if hface && !hface.deleted?
      end
      base_face = ents.grep(Sketchup::Face).find { |f| !f.deleted? }
      return false if base_face.nil?
      base_face.reverse! if base_face.normal.z > 0 # 바닥은 아래로

      # 지오메트리 추가 전에 루프(외곽+홀)를 좌표값으로 확보.
      loops = base_face.loops.map { |lp| lp.vertices.map { |v| v.position } }

      # 2) 벽: 각 루프 변마다 수직 쿼드 1면(삼각화 없음 = 깨끗한 사각면).
      loops.each do |vs|
        n = vs.length
        n.times do |k|
          a = vs[k]
          c = vs[(k + 1) % n]
          a_t = Geom::Point3d.new(a.x, a.y, a.z + h)
          c_t = Geom::Point3d.new(c.x, c.y, c.z + h)
          safe_add_face(ents, [a, c, c_t, a_t])
        end
      end

      # 3) 윗면: 외곽(+홀)을 상단 높이에 실제 면으로(n각형, 삼각화 없음).
      safe_add_face(ents, loops[0].map { |p| Geom::Point3d.new(p.x, p.y, p.z + h) })
      loops[1..-1].to_a.each do |vs|
        htop = vs.map { |p| Geom::Point3d.new(p.x, p.y, p.z + h) }
        hf = safe_add_face(ents, htop)
        hf.erase! if hf && !hf.deleted?
      end

      # 4) 재질(이 건물 그룹 면들에만 — 소규모라 저렴).
      ents.grep(Sketchup::Face).each { |f| f.material = mat; f.back_material = mat }
      true
    end

    # add_face는 퇴화 폴리곤 등에서 예외 → 조용히 건너뛴다.
    def self.safe_add_face(ents, pts)
      ents.add_face(pts)
    rescue StandardError
      nil
    end

    # 이름으로 머티리얼 찾기/생성 (재실행 시 중복 생성 방지).
    def self.material(model, name, rgb)
      mat = model.materials[name]
      unless mat
        mat = model.materials.add(name)
        mat.color = Sketchup::Color.new(rgb[0], rgb[1], rgb[2])
      end
      mat
    end

    # 이름으로 태그(레이어) 찾기/생성.
    def self.tag(model, name)
      model.layers[name] || model.layers.add(name)
    end
  end
end
