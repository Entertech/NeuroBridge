#ifndef DSP_FRAC_HEADER_FILE_GUARD
#define DSP_FRAC_HEADER_FILE_GUARD
#include "TypeDefine.h"
namespace basic
{
    namespace dsp
    {
        /// @brief 分段包络计算
        /// @param wave 波形
        /// @param fracNum 分段数量
        /// @return 下包络，上包络
        std::pair<vectord, vectord> fracEnvelopeCal(const vectord &wave, size_t fracNum);

        /// @brief 分段能量计算
        /// @param wave 波形
        /// @param fracNum 分段数量
        /// @return 
        vectord fracPowerCal(const vectord &wave, size_t fracNum);
    }
}
#endif