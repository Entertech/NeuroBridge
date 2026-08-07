#ifndef DDF66976_DD1D_46DD_9D64_657029EB52E4
#define DDF66976_DD1D_46DD_9D64_657029EB52E4
#include "TypeDefine.h"
#include "NumCpp.hpp"
#include "AffectionData.h"

namespace basic
{
    namespace affection
    {
        namespace handler
        {
            struct AiMeditationHandlerTemp
            {
                define::MeditationState meditationState;
                vectori eegQualityStore;
                std::vector<define::MeditationState> meditationStateStore;
                vectord meditationDegreeStore;

            };

            struct MeditationHandlerResult
            {
                double meditationDegree;
                define::MeditationState meditationState;
            };

            /// @brief 冥想处理器
            /// @param eegFeature 脑电特征
            /// @param eegQuality 脑电信号质量
            /// @param tmp 累计运行时长
            /// @param smoothScope 冥想度平滑范围
            /// @return 
            MeditationHandlerResult aiMeditationHandler(nc::NdArray<double> eegFeature,
                int eegQuality, double timeCum,  AiMeditationHandlerTemp &tmp, int smoothScope = 10);
        }
    }
}

#endif /* DDF66976_DD1D_46DD_9D64_657029EB52E4 */
