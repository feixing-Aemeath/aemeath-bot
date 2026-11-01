package local.assistant.core

import org.junit.Assert.*
import org.junit.Test

class CardPackingTest {
    @Test
    fun columnsMatchIPadDetailWidths() {
        val min = PadPresentation.CARD_MINIMUM_WIDTH
        assertEquals(2, CardPacking.columns(834.0 - 48, min, 12.0))
        assertEquals(3, CardPacking.columns(1194.0 - 280 - 48, min, 12.0))
        assertEquals(2, CardPacking.columns(600.0 - 48, min, 12.0))
        assertEquals(1, CardPacking.columns(500.0 - 48, min, 12.0))
    }

    @Test
    fun gapsCountTowardTheColumnThreshold() {
        assertEquals(1, CardPacking.columns(551.0, 270.0, 12.0))
        assertEquals(2, CardPacking.columns(552.0, 270.0, 12.0))
    }

    @Test
    fun invalidDimensionsStayInOneColumn() {
        listOf(Double.NaN, Double.POSITIVE_INFINITY, 0.0, -1.0).forEach {
            assertEquals(1, CardPacking.columns(it, 270.0, 12.0))
        }
        assertEquals(1, CardPacking.columns(1000.0, 0.0, 12.0))
        assertEquals(1, CardPacking.columns(1000.0, 270.0, -1.0))
    }

    @Test
    fun healthCardsGroupByHeightTallestFirst() {
        val heights = listOf(90.0, 130.0, 110.0, 250.0, 140.0, 100.0, 100.0, 120.0)
        assertEquals(listOf(listOf(3), listOf(1, 2), listOf(4, 7), listOf(0, 5), listOf(6)), CardPacking.rows(heights, 2))
        assertEquals(listOf(listOf(3), listOf(1, 2, 4), listOf(7), listOf(0, 5, 6)), CardPacking.rows(heights, 3))
    }

    @Test
    fun equalHeightCardsKeepTheirPriority() {
        assertEquals(listOf(listOf(0, 1), listOf(2, 3), listOf(4)), CardPacking.rows(List(5) { 100.0 }, 2))
    }

    @Test
    fun oneColumnStillSortsByHeightAndEmptyInputIsSafe() {
        assertEquals(listOf(listOf(1), listOf(3), listOf(2), listOf(0)), CardPacking.rows(listOf(80.0, 300.0, 120.0, 290.0), 1))
        assertTrue(CardPacking.rows(emptyList(), 3).isEmpty())
        assertEquals(listOf(listOf(0)), CardPacking.rows(listOf(50.0), 0))
    }

    @Test
    fun sizeGroupsCompareToTheirTallestCard() {
        assertEquals(listOf(listOf(1, 2), listOf(0)), CardPacking.rows(listOf(64.0, 80.0, 100.0), 3))
    }
}
