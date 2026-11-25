import unittest

from z30_extensions.features.aspect.mask import display_regions, grid_regions, regions


class MaskRegions(unittest.TestCase):
    def test_projected_narrow_photo_preserves_offscreen_vertical_edges(self):
        self.assertEqual(regions((40,-20,680,500),(0,36,720,442)),
                         ((0,36,40,442),(680,36,720,442)))

    def test_all_sides_partition_without_overlap_or_photo_coverage(self):
        viewport=(20,12,500,652)
        photo=(80,90,440,570)
        parts=regions(photo,viewport)
        self.assertEqual(sum((r-l)*(b-t) for l,t,r,b in parts),
                         480*640-360*480)
        for i,a in enumerate(parts):
            for b in (*parts[i+1:],photo):
                self.assertTrue(min(a[2],b[2])<=max(a[0],b[0]) or
                                min(a[3],b[3])<=max(a[1],b[1]))

    def test_unchanged_photo_needs_no_mask(self):
        self.assertEqual(regions((-10,-10,730,490),(0,0,720,480)),())

    def test_fail_closed_before_drawing_unknown_geometry(self):
        for photo,viewport in [((0,0,0,480),(0,0,720,480)),
                               ((800,0,900,480),(0,0,720,480)),
                               ((0,0,720,480),(-1,0,720,480)),
                               ((0,0,720.0,480),(0,0,720,480)),
                               ((0,0,720),(0,0,720,480)),
                               (None,(0,0,720,480)),
                               ((0,0,720,480),None)]:
            self.assertIsNone(regions(photo,viewport))


class MaskGridRegions(unittest.TestCase):
    def test_letterbox_record_gives_top_and_bottom_bands(self):
        self.assertEqual(grid_regions(0,720,87,306),
                         ((0,0,720,87),(0,393,720,480)))

    def test_side_record_gives_left_and_right_bars(self):
        self.assertEqual(grid_regions(40,640,0,480),
                         ((0,0,40,480),(680,0,720,480)))

    def test_small_photo_gives_four_non_overlapping_regions(self):
        parts=grid_regions(100,200,90,120)
        self.assertEqual(len(parts),4)
        for i,a in enumerate(parts):
            for b in parts[i+1:]:
                self.assertTrue(min(a[2],b[2])<=max(a[0],b[0]) or
                                min(a[3],b[3])<=max(a[1],b[1]))

    def test_full_frame_and_invalid_records_draw_nothing(self):
        for args in [(0,720,0,480),(0,0,0,480),(0,720,0,0),
                     (-4,720,0,480),(0,724,0,480),(0,720,-4,480),
                     (0,720,0,484),(0,720.0,0,480),(0,720,0,480,None)]:
            self.assertEqual(grid_regions(*args),())


class DisplayEdgeCoverage(unittest.TestCase):
    def test_wide_mask_reaches_top_and_bottom_lcd_edges(self):
        self.assertEqual(display_regions((0,106,720,370),(0,36,720,442)),
                         ((0,0,720,106),(0,370,720,480)))

    def test_narrow_side_masks_have_no_unshaded_end_segments(self):
        self.assertEqual(display_regions((40,36,680,442),(0,36,720,442)),
                         ((0,0,40,480),(680,0,720,480)))

    def test_crop_borders_stay_fixed_with_an_inset_viewport(self):
        self.assertEqual(display_regions((80,68,640,418),(24,68,696,418)),
                         ((0,0,80,480),(640,0,720,480)))
        self.assertEqual(display_regions((24,120,696,360),(24,68,696,418)),
                         ((0,0,720,120),(0,360,720,480)))

    def test_full_photo_and_bad_viewport_fail_without_extra_bars(self):
        self.assertEqual(display_regions((0,36,720,442),(0,36,720,442)),())
        self.assertIsNone(display_regions((40,36,680,442),(-2,36,720,442)))


class InteriorGridOnly(unittest.TestCase):
    def test_selected_thirds_and_quarters_keep_their_original_internal_positions(self):
        from z30_extensions.features.aspect.frame import axis
        for divisions,want in ((3,[240,478]),(4,[182,360,538])):
            _,_,first,step,count=axis(0,720,0,720,divisions,include_borders=False)
            self.assertEqual([first+i*step for i in range(count)],want)

    def test_hidden_border_is_not_removed_twice(self):
        from z30_extensions.features.aspect.frame import axis
        self.assertEqual(axis(-40,760,0,720,3,include_borders=False),
                         axis(-40,760,0,720,3))


if __name__ == '__main__':
    unittest.main()
