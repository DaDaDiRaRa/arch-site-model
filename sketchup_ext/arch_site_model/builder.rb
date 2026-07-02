# geometry JSON(로컬 미터) → SketchUp 엔티티 조립 (B1: 지형 + 건물).
#   - 지형: Geom::PolygonMesh → add_faces_from_mesh (대량 면 일괄 생성 + 소프트 엣지)
#   - 건물: footprint 면 + pushpull 돌출(위 방향 보정), 중정(홀) 처리, 층수미확인 착색
# 단위: 백엔드는 미터, SketchUp 내부는 인치 → ×M2I. 좌표축 X=동, Y=북, Z=높이.

module ArchSiteModel
  module Builder
    M2I = 39.3701 # meter → inch

    C_BUILDING = [70, 130, 180].freeze  # steel blue
    C_FLAGGED  = [210, 120, 60].freeze  # orange (층수 미확인)
    C_TERRAIN  = [120, 150, 90].freeze  # olive green

    # geometry: {"buildings"=>[...], "terrain"=>{...}|nil}
    # 반환: 생성된 건물 수
    def self.build(geometry, _warnings = [])
      model = Sketchup.active_model
      count = 0
      model.start_operation("대지모델 생성", true)
      begin
        root = model.active_entities.add_group
        root.name = "arch-site-model"
        ents = root.entities

        if geometry["terrain"]
          build_terrain(model, ents, geometry["terrain"])
        end
        count = build_buildings(model, ents, geometry["buildings"] || [])

        model.commit_operation
        model.active_view.zoom_extents
      rescue StandardError => e
        model.abort_operation
        UI.messagebox("모델 조립 오류: #{e.message}")
        raise
      end
      count
    end

    def self.build_terrain(model, parent_ents, terrain)
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
    end

    def self.build_buildings(model, parent_ents, buildings)
      mat_n = material(model, "asm_building", C_BUILDING)
      mat_f = material(model, "asm_building_unverified", C_FLAGGED)
      tag_n = tag(model, "buildings")
      tag_f = tag(model, "buildings_unverified")
      built = 0

      buildings.each do |b|
        fp = b["footprint"] || []
        height = b["height"].to_f
        next if fp.length < 3 || height <= 0

        flagged = b["flagged"] == true
        grp = parent_ents.add_group
        grp.layer = flagged ? tag_f : tag_n
        ents = grp.entities
        base = b["base_z"].to_f * M2I
        h = height * M2I

        pts = fp.map { |p| Geom::Point3d.new(p[0] * M2I, p[1] * M2I, base) }
        face = safe_add_face(ents, pts)
        next if face.nil?

        # 중정(홀): 내부 링 면을 만들고 지워서 구멍을 낸다.
        (b["holes"] || []).each do |hole|
          next if hole.length < 3
          hpts = hole.map { |p| Geom::Point3d.new(p[0] * M2I, p[1] * M2I, base) }
          hface = safe_add_face(ents, hpts)
          hface.erase! if hface && !hface.deleted?
        end

        # 홀 처리 후 남은 (구멍 뚫린) 바닥 면 재취득 → 위로 돌출.
        face = ents.grep(Sketchup::Face).find { |f| !f.deleted? }
        next if face.nil?
        dir = face.normal.z < 0 ? -h : h # 항상 +Z(위)로
        face.pushpull(dir)

        mat = flagged ? mat_f : mat_n
        ents.grep(Sketchup::Face).each { |f| f.material = mat }
        grp.name = flagged ? "building [층수미확인]" : "building"
        built += 1
      end
      built
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
